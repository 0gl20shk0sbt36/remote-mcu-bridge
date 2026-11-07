#!/usr/bin/env python3
"""STM32 ROM LAN controller; stdlib only; trusted LAN, not TLS."""
import argparse
import getpass
import ipaddress
import json
import math
import os
import select
import socket
import struct
import subprocess
import sys
import time
import zlib

SERVICE = 'stm32-rom-uart'
DISCOVER = b'STM32ROM_DISCOVER_V1'


def warn(message):
    print('discovery: ' + str(message), file=sys.stderr)


def _interface(address, prefix, name, up=True, point_to_point=False):
    """Normalize OS data, retaining excluded interfaces for --list-interfaces."""
    addr = ipaddress.IPv4Address(address)
    network = ipaddress.IPv4Network((str(addr), int(prefix)), strict=False)
    reason = ''
    if not up:
        reason = 'down'
    elif addr.is_loopback or addr.is_unspecified or addr.is_multicast:
        reason = 'non-LAN address'
    elif point_to_point or network.prefixlen >= 31:
        reason = 'point-to-point or /31-/32'
    elif addr in ipaddress.IPv4Network('100.64.0.0/10') or 'tailscale' in name.lower():
        reason = 'Tailscale/CGNAT'
    return dict(address=str(addr), prefix=network.prefixlen, interface=name,
                broadcast=str(network.broadcast_address), active=not reason,
                reason=reason)


def parse_windows_interfaces(raw):
    rows = json.loads(raw)
    if isinstance(rows, dict):
        rows = [rows]  # ConvertTo-Json emits an object for a single result.
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError('invalid Windows interface JSON')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            result.append(_interface(row['IPAddress'], row['PrefixLength'],
                                     str(row.get('InterfaceAlias', '')),
                                     row.get('Up') is True))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def parse_linux_interfaces(raw):
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError('invalid ip interface JSON')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        flags = row.get('flags', [])
        for addr in row.get('addr_info', []):
            if addr.get('family') != 'inet':
                continue
            try:
                result.append(_interface(addr['local'], addr['prefixlen'],
                                         str(row.get('ifname', '')),
                                         'UP' in flags and row.get('operstate') != 'DOWN',
                                         'POINTOPOINT' in flags))
            except (KeyError, TypeError, ValueError):
                continue
    return result


def list_interfaces():
    """Enumerate local IPv4 interfaces without third-party packages."""
    if sys.platform == 'win32':
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                   "$ErrorActionPreference = 'Stop'; "
                   "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                   "$up = @(Get-NetIPInterface -AddressFamily IPv4 | "
                   "Where-Object { $_.ConnectionState -eq 'Connected' } | "
                   "Select-Object -ExpandProperty InterfaceIndex); "
                   "Get-NetIPAddress -AddressFamily IPv4 | "
                   "Where-Object { $_.AddressState -eq 'Preferred' } | "
                   "Select-Object IPAddress,PrefixLength,InterfaceAlias,"
                   "@{Name='Up';Expression={$up -contains $_.InterfaceIndex}} | "
                   "ConvertTo-Json -Compress"]
        parser = parse_windows_interfaces
    else:
        command = ['ip', '-j', '-4', 'addr', 'show']
        parser = parse_linux_interfaces
    try:
        raw = subprocess.run(command, check=True, capture_output=True,
                             text=True, encoding='utf-8', timeout=8).stdout
        return parser(raw)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        warn('interface enumeration unavailable (%s); global broadcast fallback' % exc)
        return []


def _announcement(data, host):
    """Only validated fields can influence the subsequent control connection."""
    try:
        obj = json.loads(data)
        if not isinstance(obj, dict) or obj.get('service') != SERVICE:
            return None
        ident, port = obj.get('id'), obj.get('port')
        if not isinstance(ident, str) or not 1 <= len(ident) <= 128:
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in ident):
            return None
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        obj['host'] = str(ipaddress.IPv4Address(host))
        return obj
    except (ValueError, UnicodeError, RecursionError):
        return None


def discover(host, port, timeout, *, interfaces=None, passive_only=False):
    """Listen for beacons and query each LAN; always collect the full timeout.

    The original three positional arguments remain supported. Explicit unicast
    hosts (including localhost tests) bypass interface enumeration for queries.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('discovery timeout must be finite and nonnegative')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid discovery port')
    selected = list(dict.fromkeys(str(ipaddress.IPv4Address(i)) for i in (interfaces or [])))
    found, sockets = {}, []
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            listener.bind(('0.0.0.0', port))
            listener.setblocking(False)
            sockets.append(listener)
        except OSError as exc:
            listener.close()
            warn('passive bind on UDP %s failed (%s); active replies still available' % (port, exc))
        if not passive_only:
            if host != '255.255.255.255':
                targets = [(ip, host) for ip in selected] if selected else [('0.0.0.0', host)]
            else:
                rows = list_interfaces()
                if selected:
                    targets = []
                    for ip in selected:
                        matches = [r for r in rows if r['address'] == ip]
                        if matches:
                            targets.extend((ip, r['broadcast']) for r in matches if r['active'])
                            if not any(r['active'] for r in matches):
                                warn('%s excluded from active broadcasts' % ip)
                        elif not rows:
                            # The explicit local source still steers limited broadcast.
                            targets.append((ip, host))
                        else:
                            warn('%s is not an enumerated local IPv4 address' % ip)
                else:
                    targets = [(r['address'], r['broadcast']) for r in rows if r['active']]
                    if not rows:
                        targets = [('0.0.0.0', host)]
            for source, destination in dict.fromkeys(targets):
                query = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    query.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    query.bind((source, 0))
                    query.setblocking(False)
                    query.sendto(DISCOVER, (destination, port))
                    sockets.append(query)
                except OSError as exc:
                    query.close()
                    warn('query via %s to %s failed: %s' % (source, destination, exc))
        deadline = time.monotonic() + timeout
        while sockets:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select(sockets, [], [], remaining)
            for sock in readable:
                try:
                    data, addr = sock.recvfrom(8192)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    warn('receive failed: %s' % exc)
                    sockets.remove(sock)
                    sock.close()
                    continue
                obj = _announcement(data, addr[0])
                if obj is not None:
                    found[obj['id']] = obj
    finally:
        for sock in sockets:
            sock.close()
    return list(found.values())


DEVICES = {
    # keep in sync with rom/devices.c and tools/ramtarget/devices.json
    'f407zg': dict(flash=0x08000000, flash_max=0x100000, ram_base=0x20004000,
                   ram_max=0x1c000, sram_lo=0x20000000, sram_hi=0x20020000),
    'f103c8': dict(flash=0x08000000, flash_max=0x10000, ram_base=0x20000400,
                   ram_max=0x4c00, sram_lo=0x20000000, sram_hi=0x20005000),
}
DEFAULT_DEVICE = 'f407zg'


def image(path, ram, device=DEFAULT_DEVICE):
    dev = DEVICES[device]
    data = open(path, 'rb').read()
    base, limit = (dev['ram_base'], dev['ram_max']) if ram else (dev['flash'], dev['flash_max'])
    if not 8 <= len(data) <= limit:
        raise ValueError('image size out of range for %s' % device)
    sp, pc = struct.unpack_from('<II', data)
    lo = dev['ram_base'] if ram else dev['sram_lo']
    if sp % 8 or not lo < sp <= dev['sram_hi']:
        raise ValueError('invalid initial stack')
    if ram and sp < base + len(data):
        raise ValueError('initial stack inside the image; stack would overwrite code')
    if not pc & 1 or not base <= (pc & ~1) < base + len(data):
        raise ValueError('Thumb reset entry outside image; relink for target')
    return data


def line(stream):
    raw = stream.readline(4097)
    if not raw.endswith(b'\n') or len(raw) > 4096:
        raise ValueError('invalid response framing')
    obj = json.loads(raw)
    if not obj.get('ok'):
        raise RuntimeError(str(obj.get('error', obj)))
    return obj


def control(host, port, token, op, data=None, timeout=120, fragment=0):
    if op == 'probe' and data is not None:
        raise ValueError('probe does not accept an image')
    req = {'op': op, 'token': token}
    if data is not None:
        req.update(size=len(data), crc32=zlib.crc32(data))
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        wire = (json.dumps(req, separators=(',', ':')) + '\n').encode()
        def send(buf):
            for i in range(0, len(buf), fragment or len(buf)):
                s.sendall(buf[i:i + (fragment or len(buf))])
        send(wire)
        with s.makefile('rb') as f:
            response = line(f)
            if data is not None:
                if not response.get('ready'):
                    raise RuntimeError('server not ready')
                send(data)
                response = line(f)
            return response


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('op', nargs='?', choices=['discover', 'status', 'restart', 'probe', 'flash', 'ram'],
                   help='probe: authenticated ROM UART diagnostics at fixed 9600 baud; no image')
    p.add_argument('--interface', action='append', default=[], metavar='IPv4',
                   help='local IPv4 source for active discovery; repeat for multiple NICs')
    p.add_argument('--list-interfaces', action='store_true',
                   help='list local IPv4 addresses, broadcast targets and exclusions, then exit')
    p.add_argument('--passive-only', action='store_true',
                   help='only listen for ESP beacons; do not send discovery queries')
    p.add_argument('--discovery-timeout', type=float, default=None, metavar='SECONDS',
                   help='full collection window: default 32s for discover (30s beacons), '
                        '3s for automatic device selection; quick replies do not end it early')
    p.add_argument('image', nargs='?')
    p.add_argument('--host', help='discovered IP; default discovery broadcast')
    p.add_argument('--port', type=int, default=3333)
    p.add_argument('--discovery-port', type=int, default=3334)
    p.add_argument('--timeout', type=float, default=120)
    p.add_argument('--token-env', default='STM32_ROM_TOKEN')
    p.add_argument('--device', choices=sorted(DEVICES), default=DEFAULT_DEVICE,
                   help='target part used for local image validation')
    a = p.parse_args(argv)
    if not a.op and not a.list_interfaces:
        p.error('an operation or --list-interfaces is required')
    if a.op == 'probe' and a.image is not None:
        p.error('probe does not accept an image')
    try:
        if a.list_interfaces:
            print(json.dumps(list_interfaces(), ensure_ascii=False))
            return 0
        discovery_timeout = a.discovery_timeout
        if discovery_timeout is None:
            discovery_timeout = 32 if a.op == 'discover' else 3
        if not math.isfinite(discovery_timeout) or discovery_timeout < 0:
            raise ValueError('discovery timeout must be finite and nonnegative')
        interfaces = [str(ipaddress.IPv4Address(ip)) for ip in a.interface]
        if a.op == 'discover':
            result = discover(a.host or '255.255.255.255', a.discovery_port,
                              discovery_timeout, interfaces=interfaces,
                              passive_only=a.passive_only)
        else:
            host, port = a.host, a.port
            if not host:
                devices = discover('255.255.255.255', a.discovery_port,
                                   discovery_timeout, interfaces=interfaces,
                                   passive_only=a.passive_only)
                if len(devices) != 1:
                    raise ValueError('discovery needs exactly one device; select --host explicitly')
                host, port = devices[0]['host'], devices[0]['port']
            token = os.environ.get(a.token_env) or getpass.getpass('LAN token: ')
            if not token:
                raise ValueError('empty token forbidden')
            payload = image(a.image, a.op == 'ram', a.device) if a.op in ('flash', 'ram') and a.image else None
            if a.op in ('flash', 'ram') and payload is None:
                raise ValueError('image path required')
            result = control(host, port, token, a.op, payload, a.timeout)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        p.exit(1, str(exc) + '\n')

if __name__ == '__main__':
    main()
