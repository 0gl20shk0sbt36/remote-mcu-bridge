#!/usr/bin/env python3
"""Authenticated LAN diagnostic streams (stdlib, Python >=3.10; no TLS).

Framed UART remains connected across probe/flash/ram/restart preemption.
Opening a stream never resets the target. Logs use 3335; application UART uses 3336 8N1.
"""
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import socket
import select
import struct
import sys
import threading
import time


class StreamError(Exception):
    pass


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-JSON constant')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def load_token(token_file=None):
    token = '' if token_file else os.environ.get('STM32_ROM_TOKEN', '').strip()
    if not token:
        try:
            token = (Path(token_file) if token_file else Path(__file__).with_name('token.txt')).read_text(encoding='utf-8-sig').strip()
        except FileNotFoundError:
            raise StreamError('Set STM32_ROM_TOKEN or create companion token.txt') from None
    if not token or '\n' in token or '\r' in token:
        raise StreamError('Invalid or empty token configuration')
    return token


def resolve_host(host, timeout=2.0):
    if host:
        return host
    from romctl import discover
    devices = discover('255.255.255.255', 3334, timeout)
    if len(devices) != 1:
        raise StreamError('Discovery needs exactly one device; use --host with the ESP IP')
    return devices[0]['host']


def open_stream(host, port, token, op, baud=115200, timeout=5.0, return_ack=False):
    """Read exactly the JSON ack line, retaining coalesced raw stream bytes."""
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        request = {'token': token, 'op': op}
        if op in ('uart', 'uart-framed'):
            request['baud'] = baud
        sock.sendall(json.dumps(request, separators=(',', ':')).encode('utf-8') + b'\n')
        data = bytearray()
        deadline = time.monotonic() + timeout
        while b'\n' not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StreamError('Handshake timed out')
            sock.settimeout(remaining)
            chunk = sock.recv(4096)
            if not chunk:
                raise StreamError('Connection closed before handshake')
            data.extend(chunk)
            if len(data.split(b'\n', 1)[0]) > 8192:
                raise StreamError('Oversized handshake')
        header, pending = bytes(data).split(b'\n', 1)
        try:
            ack = strict_json(header)
        except (ValueError, UnicodeError):
            raise StreamError('Invalid server handshake') from None
        if not isinstance(ack, dict) or ack.get('ok') is not True:
            # Do not echo untrusted server fields: they could contain credentials.
            raise StreamError('Stream rejected (authentication, busy, or unsupported request)')
        sock.settimeout(0.2)
        return (sock, pending, ack) if return_ack else (sock, pending)
    except BaseException:
        sock.close()
        raise


def input_worker(sock, stop, errors, raw_input=False):
    """Daemon so blocked redirected stdin cannot prevent Ctrl-C exit."""
    try:
        if os.name == 'nt' and sys.stdin.isatty() and not raw_input:
            import msvcrt
            while not stop.is_set():
                if not msvcrt.kbhit():
                    stop.wait(0.02)
                    continue
                char = msvcrt.getwch()
                if char == '\x03':
                    stop.set()
                    return
                if char in ('\x00', '\xe0'):
                    msvcrt.getwch()  # discard extended-key scan code
                    continue
                sock.sendall(('\n' if char == '\r' else char).encode('utf-8'))
        else:
            # Never block on Python's buffered stdin lock: a daemon holding it
            # can abort the interpreter during shutdown. POSIX select is finite;
            # Windows redirected pipes use an unbuffered OS read in the daemon.
            import select
            fd = sys.stdin.fileno()
            while not stop.is_set():
                if os.name != 'nt' and not select.select([fd], [], [], 0.1)[0]:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    return  # keep collecting UART output until duration/Ctrl-C
                sock.sendall(chunk)
    except (OSError, ValueError) as exc:
        if not stop.is_set():
            errors.append(type(exc).__name__)
            stop.set()


def receive_stream(sock, pending=b'', duration=None, output=None, save=None,
                   interactive=False, raw_input=False):
    output = output or getattr(sys.stdout, 'buffer', sys.stdout)
    stop, errors = threading.Event(), []
    worker = None
    if interactive:
        worker = threading.Thread(target=input_worker, args=(sock, stop, errors, raw_input), daemon=True)
        worker.start()
    deadline = None if duration is None else time.monotonic() + duration
    def emit(data):
        if not data:
            return
        try:
            output.write(data)
        except TypeError:
            output.write(data.decode('utf-8', errors='replace'))
        output.flush()
        if save is not None:
            save.write(data)
            save.flush()
    try:
        emit(pending)
        while not stop.is_set():
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(min(0.2, remaining))
            else:
                sock.settimeout(0.2)
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            emit(data)
    finally:
        stop.set()
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        sock.close()
        if worker:
            worker.join(timeout=0.3)
    if errors:
        raise StreamError('UART input failed: ' + errors[0])


def valid_epoch(value):
    return type(value) is int and 0 <= value <= 0xffffffff


def record(kind, payload):
    if kind not in (1, 2) or len(payload) > 1028 or (kind == 1 and len(payload) < 4):
        raise StreamError('Invalid UART record')
    return struct.pack('!BH', kind, len(payload)) + payload


class FrameParser:
    """Bounded incremental parser: TCP packet boundaries have no meaning."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        while len(self.buffer) >= 3:
            kind, size = struct.unpack('!BH', self.buffer[:3])
            if kind not in (1, 2) or size > 1028 or (kind == 1 and size < 4) or (kind == 2 and not size):
                raise StreamError('Invalid UART frame type or length')
            if len(self.buffer) < 3 + size:
                return
            payload = bytes(self.buffer[3:3 + size])
            del self.buffer[:3 + size]
            yield kind, payload


class FramedUART:
    def __init__(self, sock, ack, diagnostic=None):
        if (type(ack.get('framed')) is not int or ack.get('framed') != 1
                or not valid_epoch(ack.get('epoch')) or type(ack.get('paused')) is not bool):
            raise StreamError('Server does not support framed UART; explicit --legacy-raw is required')
        self.sock, self.epoch, self.paused = sock, ack['epoch'], ack['paused']
        self.lock = threading.Lock()
        self.parser = FrameParser()
        self.diagnostic = diagnostic or (lambda text: print('wifidebug: ' + text, file=sys.stderr, flush=True))

    def send(self, data):
        # No application queue: pause consumes and rejects bytes, never replays.
        with self.lock:
            if self.paused:
                self.diagnostic('paused: rejected %d input bytes (not queued)' % len(data))
                return
            for offset in range(0, len(data), 1024):
                self.sock.sendall(record(1, struct.pack('!I', self.epoch) + data[offset:offset + 1024]))

    def receive(self, data, emit):
        for kind, payload in self.parser.feed(data):
            with self.lock:
                if kind == 1:
                    epoch = struct.unpack('!I', payload[:4])[0]
                    if not self.paused and epoch == self.epoch:
                        emit(payload[4:])
                    continue
                try:
                    status = strict_json(payload)
                except (ValueError, UnicodeError):
                    raise StreamError('Invalid UART status JSON') from None
                if (not isinstance(status, dict) or not valid_epoch(status.get('epoch'))
                        or status.get('event') not in ('paused', 'resumed', 'write-rejected')):
                    raise StreamError('Invalid UART status fields')
                event = status['event']
                if event in ('paused', 'resumed'):
                    self.epoch = status['epoch']
                    self.paused = event == 'paused'
                # Never print arbitrary server text (including possible secrets).
                self.diagnostic('%s epoch=%d' % (event, status['epoch']))


@contextlib.contextmanager
def virtual_pty(link=None):
    if not sys.platform.startswith('linux'):
        raise StreamError('--pty requires Linux')
    import pty
    import tty
    master, slave = pty.openpty()
    created = False
    try:
        tty.setraw(slave)
        os.set_blocking(master, False)
        path = os.ttyname(slave)
        if link:
            # Atomic symlink creation refuses files and dangling symlinks alike.
            os.symlink(path, link)
            created = True
        print('wifidebug: PTY ' + path, file=sys.stderr, flush=True)
        if link:
            print('wifidebug: PTY link ' + str(link), file=sys.stderr, flush=True)
        yield master
    finally:
        if created and link is not None and os.path.islink(link) and os.readlink(link) == os.ttyname(slave):
            os.unlink(link)
        os.close(master)
        os.close(slave)  # retain slave until exit to avoid master EIO spinning


def binary_stdio():
    if os.name == 'nt':
        import msvcrt
        for stream in (sys.stdin, sys.stdout):
            msvcrt.setmode(stream.fileno(), os.O_BINARY)


def receive_framed(session, pending=b'', duration=None, output=None, save=None,
                   input_fd=None, pty_fd=None, send=None, console=False):
    output = output if output is not None else sys.stdout.buffer
    stop, errors = threading.Event(), []
    worker = None
    deadline = None if duration is None else time.monotonic() + duration

    def emit(data):
        if pty_fd is not None:
            view = memoryview(data)
            while view and not stop.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    return
                if not select.select([], [pty_fd], [], .1)[1]:
                    continue
                try:
                    view = view[os.write(pty_fd, view):]
                except BlockingIOError:
                    continue
        else:
            output.write(data)
            output.flush()
        if save is not None:
            save.write(data)
            save.flush()

    def windows_input():
        try:
            if console:
                import msvcrt
                while not stop.is_set():
                    if not msvcrt.kbhit():
                        stop.wait(.02)
                        continue
                    char = msvcrt.getwch()
                    if char == '\x03':
                        stop.set()
                        return
                    if char in ('\x00', '\xe0'):
                        msvcrt.getwch()
                        continue
                    session.send(('\n' if char == '\r' else char).encode('utf-8'))
            else:
                while not stop.is_set():
                    assert input_fd is not None
                    chunk = os.read(input_fd, 1024)
                    if not chunk:
                        break
                    session.send(chunk)
        except OSError:
            if not stop.is_set():
                errors.append('UART input failed')
                stop.set()

    try:
        session.receive(pending, emit)
        if send is not None:
            session.send(send)
        if os.name == 'nt' and input_fd is not None:
            worker = threading.Thread(target=windows_input, daemon=True)
            worker.start()
        while not stop.is_set():
            remaining = .1 if deadline is None else min(.1, deadline - time.monotonic())
            if remaining <= 0:
                break
            reads = [session.sock]
            if os.name != 'nt' and input_fd is not None:
                reads.append(input_fd)
            ready = select.select(reads, [], [], remaining)[0]
            # Drain local input before a pending resume: paused bytes are not replayed.
            if input_fd is not None and input_fd in ready:
                chunk = os.read(input_fd, 1024)
                if chunk:
                    session.send(chunk)
                    # Consume an arbitrarily large paused pipe backlog in bounded
                    # iterations BEFORE processing resume; never replay leftovers.
                    if session.paused:
                        continue
                else:
                    input_fd = None
            if session.sock in ready:
                data = session.sock.recv(4096)
                if not data:
                    if session.parser.buffer:
                        raise StreamError('Truncated UART frame')
                    break
                session.receive(data, emit)
    finally:
        stop.set()
        with contextlib.suppress(OSError):
            session.sock.shutdown(socket.SHUT_RDWR)
        session.sock.close()
        if worker:
            worker.join(.3)
    if errors:
        raise StreamError(errors[0])


def finite_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('must be finite and nonnegative')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=Path, help='Private token file (overrides environment)')
    parser.add_argument('--host', help='ESP IP (omit for LAN discovery)')
    parser.add_argument('--port', type=int, help='Override diagnostic TCP port')
    parser.add_argument('--timeout', type=finite_seconds, default=5.0, help='Handshake timeout seconds')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('logs', 'uart', 'exec', 'test'):
        command = sub.add_parser(name)
        # Permit these options before OR after the subcommand.
        command.add_argument('--host', default=argparse.SUPPRESS)
        command.add_argument('--port', type=int, default=argparse.SUPPRESS)
        command.add_argument('--token-file', type=Path, default=argparse.SUPPRESS)
        if name != 'logs':
            command.add_argument('--legacy-raw', action='store_true')
        if name == 'uart':
            modes = command.add_mutually_exclusive_group()
            modes.add_argument('--stdio', action='store_true', help='Binary process stdin/stdout')
            modes.add_argument('--pty', action='store_true', help='Linux virtual serial port')
            command.add_argument('--pty-link', type=Path, help='Create user-owned symlink; refuse existing')
        command.add_argument('--duration', type=finite_seconds,
                             default=3.0 if name in ('exec', 'test') else None)
        command.add_argument('--save', type=Path, help='Write raw received bytes to this file')
        if name != 'logs':
            command.add_argument('--baud', type=int, default=115200)
            command.add_argument('--send', required=name in ('exec', 'test'),
                                 help='UTF-8 text sent exactly as provided (no added newline)')
    args = parser.parse_args(argv)
    if args.timeout <= 0 or (args.port is not None and not 1 <= args.port <= 65535):
        parser.error('timeout must be positive; port must be 1..65535')
    if getattr(args, 'baud', 115200) <= 0:
        parser.error('baud must be positive')
    if getattr(args, 'legacy_raw', False) and getattr(args, 'pty', False):
        parser.error('--pty requires framed mode; remove --legacy-raw')
    if getattr(args, 'pty_link', None) and not args.pty:
        parser.error('--pty-link requires --pty')
    try:
        token = load_token(args.token_file)
        host = resolve_host(args.host)
        op = 'logs' if args.command == 'logs' else ('uart' if args.legacy_raw else 'uart-framed')
        port = args.port or (3335 if op == 'logs' else 3336)
        with contextlib.ExitStack() as stack:
            save = stack.enter_context(args.save.open('wb')) if args.save else None
            if getattr(args, 'stdio', False):
                binary_stdio()
            sock, pending, ack = open_stream(host, port, token, op,
                                        getattr(args, 'baud', 115200), args.timeout, return_ack=True)
            stack.callback(sock.close)
            send = getattr(args, 'send', None)
            if op == 'uart-framed':
                session = FramedUART(sock, ack)
                master = stack.enter_context(virtual_pty(args.pty_link)) if getattr(args, 'pty', False) else None
                input_fd = master if master is not None else (sys.stdin.fileno() if args.command == 'uart' else None)
                receive_framed(session, pending, args.duration, save=save, input_fd=input_fd,
                               pty_fd=master, send=None if send is None else send.encode('utf-8'),
                               console=not getattr(args, 'stdio', False) and sys.stdin.isatty())
            else:
                if op == 'uart':
                    print('wifidebug: legacy raw: programming may disconnect this session', file=sys.stderr)
                if send is not None:
                    sock.sendall(send.encode('utf-8'))
                receive_stream(sock, pending, args.duration, save=save,
                               interactive=args.command == 'uart', raw_input=getattr(args, 'stdio', False))
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, StreamError) as exc:
        # StreamError messages never contain the token or server JSON.
        print('wifidebug: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
