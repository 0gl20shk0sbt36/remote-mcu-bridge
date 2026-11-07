"""Stdlib discovery tests: real localhost UDP plus mocked OS/NIC metadata."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('romctl', Path(__file__).resolve().parents[1] / 'tools/romctl.py')
assert spec is not None and spec.loader is not None
romctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(romctl)


def packet(ident='esp-1', **extra):
    return json.dumps(dict(service=romctl.SERVICE, id=ident, port=3333, **extra)).encode()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class DiscoveryTests(unittest.TestCase):
    def test_passive_garbage_dedup_and_full_window(self):
        port = free_port()
        def sender():
            time.sleep(.04)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                garbage = [b'[]', b'null', b'bad', b'\xff', b'{"id": []}',
                           json.dumps(dict(service=romctl.SERVICE, id=[], port=3333)).encode(),
                           json.dumps(dict(service=romctl.SERVICE, id='bad', port=True)).encode(),
                           json.dumps(dict(service=romctl.SERVICE, id='bad', port=70000)).encode()]
                for data in garbage + [packet(), packet(host='attacker.invalid')]:
                    sock.sendto(data, ('127.0.0.1', port))
                time.sleep(.10)
                sock.sendto(packet('esp-2'), ('127.0.0.1', port))
        thread = threading.Thread(target=sender)
        thread.start()
        start = time.monotonic()
        with patch.object(romctl, 'list_interfaces') as enum:
            result = romctl.discover('255.255.255.255', port, .25, passive_only=True)
            enum.assert_not_called()
        thread.join()
        self.assertGreaterEqual(time.monotonic() - start, .24)
        self.assertEqual({r['id'] for r in result}, {'esp-1', 'esp-2'})
        self.assertEqual({r['host'] for r in result}, {'127.0.0.1'})

    def active(self, interfaces=None, rows=None):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        server.settimeout(1)
        port = server.getsockname()[1]
        sources = []
        expected = len([r for r in rows if r['active'] and r['address'].startswith('127.')]) if rows else 1
        def responder():
            try:
                for _ in range(expected):
                    data, addr = server.recvfrom(4096)
                    sources.append(addr[0])
                    if data == romctl.DISCOVER:
                        server.sendto(packet('esp-' + addr[0]), addr)
            finally:
                server.close()
        thread = threading.Thread(target=responder)
        thread.start()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), patch.object(romctl, 'list_interfaces', return_value=rows or []):
            result = romctl.discover('255.255.255.255' if rows else '127.0.0.1',
                                     port, .18, interfaces=interfaces)
        thread.join()
        return result, sources, stderr.getvalue()

    def test_active_occupied_listener_and_explicit_interface(self):
        result, sources, warning = self.active(['127.0.0.2'])
        self.assertEqual(sources, ['127.0.0.2'])
        self.assertEqual(len(result), 1)
        self.assertIn('passive bind', warning)

    def test_multiple_interfaces_one_failing_bind(self):
        rows = [dict(address=ip, broadcast='127.0.0.1', active=True)
                for ip in ['192.0.2.99', '127.0.0.2', '127.0.0.3']]
        result, sources, warning = self.active(rows=rows)
        self.assertEqual(set(sources), {'127.0.0.2', '127.0.0.3'})
        self.assertEqual(len(result), 2)
        self.assertIn('query via 192.0.2.99', warning)

    def test_explicit_interface_filters_enumeration(self):
        rows = [dict(address=ip, broadcast='127.0.0.1', active=True)
                for ip in ['127.0.0.2', '192.0.2.99']]
        result, sources, _ = self.active(interfaces=['127.0.0.2'], rows=rows)
        self.assertEqual(sources, ['127.0.0.2'])
        self.assertEqual(len(result), 1)

    def test_windows_singleton_and_filters(self):
        row = dict(IPAddress='198.51.100.4', PrefixLength=24, InterfaceAlias='Wi-Fi', Up=True)
        parsed = romctl.parse_windows_interfaces(json.dumps(row))
        self.assertEqual(parsed[0]['broadcast'], '198.51.100.255')
        self.assertTrue(parsed[0]['active'])
        rows = [row] + [dict(row, **change) for change in [
            dict(IPAddress='100.100.1.1', PrefixLength=10), dict(InterfaceAlias='Tailscale'),
            dict(Up=False), dict(PrefixLength=31), dict(PrefixLength=32),
            dict(IPAddress='127.0.0.1')]]
        parsed = romctl.parse_windows_interfaces(json.dumps(rows))
        self.assertEqual(sum(r['active'] for r in parsed), 1)
        with patch.object(romctl.sys, 'platform', 'win32'), patch.object(romctl.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(row))) as run:
            self.assertEqual(romctl.list_interfaces()[0]['address'], '198.51.100.4')
            command = run.call_args.args[0]
            self.assertEqual(command[0], 'powershell.exe')
            self.assertIn('Get-NetIPAddress', command[-1])
            self.assertIn('Get-NetIPInterface', command[-1])

    def test_linux_filters(self):
        rows = [dict(ifname='eth0', flags=['UP'], operstate='UP', addr_info=[dict(family='inet', local='10.2.3.4', prefixlen=24)]),
                dict(ifname='tun0', flags=['UP', 'POINTOPOINT'], addr_info=[dict(family='inet', local='10.3.4.5', prefixlen=24)])]
        parsed = romctl.parse_linux_interfaces(json.dumps(rows))
        self.assertTrue(parsed[0]['active'])
        self.assertFalse(parsed[1]['active'])

    def test_global_fallback(self):
        real_socket = socket.socket
        sends = []
        class RecordingSocket:
            def __init__(self, *args):
                self.sock = real_socket(*args)
            def __getattr__(self, name):
                return getattr(self.sock, name)
            def sendto(self, data, addr):
                sends.append(addr)
                return len(data)
        port = free_port()
        with patch.object(romctl, 'list_interfaces', return_value=[]), patch.object(romctl.socket, 'socket', RecordingSocket):
            self.assertEqual(romctl.discover('255.255.255.255', port, 0), [])
        self.assertEqual(sends[0][0], '255.255.255.255')

    def test_enumeration_failure_and_excluded_only(self):
        with patch.object(romctl.subprocess, 'run', side_effect=FileNotFoundError('missing ip')), contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(romctl.list_interfaces(), [])
            self.assertIn('fallback', stderr.getvalue())
        port = free_port()
        with patch.object(romctl, 'list_interfaces', return_value=[dict(address='100.90.1.2', broadcast='100.127.255.255', active=False)]), patch.object(romctl.socket, 'socket', wraps=socket.socket) as factory:
            self.assertEqual(romctl.discover('255.255.255.255', port, 0), [])
            self.assertEqual(factory.call_count, 1)  # passive listener only

    def test_passive_occupied_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
            occupied.bind(('0.0.0.0', 0))
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(romctl.discover('255.255.255.255', occupied.getsockname()[1], .01, passive_only=True), [])
            self.assertIn('passive bind', stderr.getvalue())

    def test_invalid_announcement_fields(self):
        for ident in [[], {}, 3, None, '', 'bad\nname']:
            self.assertIsNone(romctl._announcement(packet(ident), '127.0.0.1'))
        for port in [[], {}, True, None, 0, -1, 65536, '3333', 3.5]:
            data = json.dumps(dict(service=romctl.SERVICE, id='ok', port=port)).encode()
            self.assertIsNone(romctl._announcement(data, '127.0.0.1'))

    def test_cli_defaults_and_repeated_interfaces(self):
        with patch.object(romctl, 'discover', return_value=[]) as discover, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(romctl.main(['discover', '--interface', '192.0.2.2', '--interface', '203.0.113.2']), 0)
            self.assertEqual(discover.call_args.args[2], 32)
            self.assertEqual(discover.call_args.kwargs['interfaces'], ['192.0.2.2', '203.0.113.2'])
            romctl.main(['discover', '--discovery-timeout', '.1', '--passive-only'])
            self.assertEqual(discover.call_args.args[2], .1)
            self.assertTrue(discover.call_args.kwargs['passive_only'])
        with patch.object(romctl, 'list_interfaces', return_value=[]) as enum, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(romctl.main(['--list-interfaces']), 0)
            enum.assert_called_once()
        with patch.object(romctl, 'discover', return_value=[]) as discover:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                romctl.main(['status'])
            self.assertEqual(discover.call_args.args[2], 3)


if __name__ == '__main__':
    unittest.main()
