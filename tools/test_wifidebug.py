"""Real localhost TCP fixtures; no ESP or physical UART claim."""

import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest

import wifidebug


class Server:
    def __init__(self, handler):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(3)
        self.port = self.listener.getsockname()[1]
        self.errors = []
        self.closed = threading.Event()

        def run():
            try:
                with self.listener:
                    conn, _ = self.listener.accept()
                    with conn:
                        conn.settimeout(3)
                        line = bytearray()
                        while not line.endswith(b"\n"):
                            chunk = conn.recv(1)
                            if not chunk:
                                raise AssertionError("incomplete request")
                            line.extend(chunk)
                        handler(conn, json.loads(line), self.closed)
            except BaseException as exc:
                self.errors.append(exc)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def check(self):
        self.thread.join(4)
        assert not self.thread.is_alive(), "server still running"
        if self.errors:
            raise self.errors[0]


class Tests(unittest.TestCase):
    def test_bidirectional_coalesced_header_and_raw(self):
        def handler(conn, req, closed):
            self.assertEqual(req, dict(token="fixture", op="uart", baud=115200))
            conn.sendall(b'{"ok":true}\nAPP SRAM heartbeat 1\r\n')
            self.assertEqual(conn.recv(64), b"ping\n")
            conn.sendall(b"PONG SRAM\r\n\x00\xff")

        server = Server(handler)
        sock, pending = wifidebug.open_stream("127.0.0.1", server.port, "fixture", "uart")
        sock.sendall(b"ping\n")
        output, saved = io.BytesIO(), io.BytesIO()
        wifidebug.receive_stream(sock, pending, 1, output, saved)
        self.assertEqual(output.getvalue(), b"APP SRAM heartbeat 1\r\nPONG SRAM\r\n\x00\xff")
        self.assertEqual(saved.getvalue(), output.getvalue())
        server.check()

    def test_auth_rejection_hides_secret_and_closes(self):
        def handler(conn, req, closed):
            conn.sendall(b'{"ok":false,"token":"fixture-secret"}\n')
            self.assertEqual(conn.recv(1), b"")
            closed.set()

        server = Server(handler)
        with self.assertRaises(wifidebug.StreamError) as caught:
            wifidebug.open_stream("127.0.0.1", server.port, "fixture-secret", "logs")
        self.assertNotIn("fixture-secret", str(caught.exception))
        server.check()
        self.assertTrue(server.closed.is_set())

    def test_duration_closes_idle_socket(self):
        def handler(conn, req, closed):
            conn.sendall(b'{"ok":true}\n')
            self.assertEqual(conn.recv(1), b"")
            closed.set()

        server = Server(handler)
        sock, pending = wifidebug.open_stream("127.0.0.1", server.port, "fixture", "logs")
        start = time.monotonic()
        wifidebug.receive_stream(sock, pending, 0.1, io.BytesIO())
        self.assertLess(time.monotonic() - start, 0.8)
        server.check()
        self.assertTrue(server.closed.is_set())

    def test_handshake_timeout_closes(self):
        def handler(conn, req, closed):
            self.assertEqual(conn.recv(1), b"")
            closed.set()

        server = Server(handler)
        with self.assertRaises((socket.timeout, wifidebug.StreamError)):
            wifidebug.open_stream("127.0.0.1", server.port, "fixture", "logs", timeout=0.1)
        server.check()
        self.assertTrue(server.closed.is_set())

    def test_fragmented_ack(self):
        def handler(conn, req, closed):
            conn.sendall(b'{"ok":')
            time.sleep(0.02)
            conn.sendall(b"true}\nlogs\n")

        server = Server(handler)
        sock, pending = wifidebug.open_stream("127.0.0.1", server.port, "fixture", "logs")
        output = io.BytesIO()
        wifidebug.receive_stream(sock, pending, 1, output)
        self.assertEqual(output.getvalue(), b"logs\n")
        server.check()

    def test_cli_exec_real_socket(self):
        def handler(conn, req, closed):
            self.assertEqual(req["op"], "uart")
            conn.sendall(b'{"ok":true}\n')
            self.assertEqual(conn.recv(64), b"ping\n")
            conn.sendall(b"PONG SRAM\r\nAPP SRAM heartbeat 2\r\n")
            self.assertEqual(conn.recv(1), b"")

        server = Server(handler)
        env = dict(os.environ, STM32_ROM_TOKEN="fixture-secret")
        result = subprocess.run(
            [
                sys.executable,
                str(Path(wifidebug.__file__)),
                "exec",
                "--legacy-raw",
                "--host",
                "127.0.0.1",
                "--port",
                str(server.port),
                "--send",
                "ping\n",
                "--duration",
                ".15",
            ],
            env=env,
            capture_output=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"PONG SRAM", result.stdout)
        self.assertNotIn(b"fixture-secret", result.stdout + result.stderr)
        server.check()

    @unittest.skipIf(os.name == "nt", "POSIX signal fixture")
    def test_ctrl_c_clean_close(self):
        import signal

        ready = threading.Event()
        marker = b"ctrl-c-ready\n"

        def handler(conn, req, closed):
            conn.sendall(b'{"ok":true}\n')
            # sendall(ACK) is not proof the client consumed it. The real CLI
            # starts its stdin worker only AFTER open_stream validates the ACK.
            # Read a full marker (TCP may fragment it) before allowing SIGINT;
            # send no more bytes, so the clean-close assertion still requires FIN.
            received = bytearray()
            while len(received) < len(marker):
                chunk = conn.recv(len(marker) - len(received))
                self.assertTrue(chunk, "client closed before readiness marker")
                received.extend(chunk)
            self.assertEqual(received, marker)
            ready.set()
            self.assertEqual(conn.recv(1), b"")
            closed.set()

        server = Server(handler)
        env = dict(os.environ, STM32_ROM_TOKEN="fixture-secret")
        proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(wifidebug.__file__)),
                "uart",
                "--legacy-raw",
                "--host",
                "127.0.0.1",
                "--port",
                str(server.port),
            ],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            proc.stdin.write(marker)
            proc.stdin.flush()
            self.assertTrue(ready.wait(2), "CLI did not consume ACK and forward stdin")
            proc.send_signal(signal.SIGINT)
            out, err = proc.communicate(timeout=2)
            self.assertEqual(proc.returncode, 130, err)
            self.assertEqual(out, b"")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        server.check()
        self.assertTrue(server.closed.is_set())
        self.assertEqual(server.listener.fileno(), -1)

    @unittest.skipIf(os.name == "nt", "POSIX signal fixture")
    def test_ctrl_c_during_unread_handshake(self):
        import signal

        requested, send_ack, ack_sent = (threading.Event() for _ in range(3))

        def handler(conn, req, closed):
            requested.set()
            self.assertTrue(send_ack.wait(2))
            conn.sendall(b'{"ok":true}\n')
            ack_sent.set()
            # Unlike an established stream, cancellation with an unread ACK may
            # close via TCP reset. Only this early-handshake test permits it.
            try:
                self.assertEqual(conn.recv(1), b"")
            except ConnectionResetError:
                pass
            closed.set()

        server = Server(handler)
        proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(wifidebug.__file__)),
                "uart",
                "--legacy-raw",
                "--host",
                "127.0.0.1",
                "--port",
                str(server.port),
            ],
            env=dict(os.environ, STM32_ROM_TOKEN="fixture-secret"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            self.assertTrue(requested.wait(2))
            # Force the old race ordering: ACK queues while the CLI cannot read
            # it, then SIGINT is pending before the client is resumed. No sleep.
            proc.send_signal(signal.SIGSTOP)
            pid, status = os.waitpid(proc.pid, os.WUNTRACED)
            self.assertEqual(pid, proc.pid)
            self.assertTrue(os.WIFSTOPPED(status))
            send_ack.set()
            self.assertTrue(ack_sent.wait(2))
            proc.send_signal(signal.SIGINT)
            proc.send_signal(signal.SIGCONT)
            out, err = proc.communicate(timeout=2)
            self.assertEqual(proc.returncode, 130, err)
            self.assertEqual(out, b"")
        finally:
            send_ack.set()
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        server.check()
        self.assertTrue(server.closed.is_set())
        self.assertEqual(server.listener.fileno(), -1)

    def test_daemon_input_clean_exit(self):
        def handler(conn, req, closed):
            conn.sendall(b'{"ok":true}\n')
            self.assertEqual(conn.recv(1), b"")

        server = Server(handler)
        env = dict(os.environ, STM32_ROM_TOKEN="fixture-secret")
        proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(wifidebug.__file__)),
                "uart",
                "--legacy-raw",
                "--host",
                "127.0.0.1",
                "--port",
                str(server.port),
                "--duration",
                ".1",
            ],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(proc.wait(timeout=2), 0)
        finally:
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
            if proc.poll() is None:
                proc.kill()
        server.check()


def data_frame(epoch, data):
    import struct

    return wifidebug.record(1, struct.pack("!I", epoch) + data)


def status_frame(event, epoch):
    return wifidebug.record(2, json.dumps(dict(event=event, epoch=epoch)).encode())


def read_frame(conn):
    import struct

    def exact(size):
        data = b""
        while len(data) < size:
            chunk = conn.recv(size - len(data))
            if not chunk:
                raise AssertionError("early EOF")
            data += chunk
        return data

    kind, size = struct.unpack("!BH", exact(3))
    assert kind == 1 and 4 <= size <= 1028
    payload = exact(size)
    return struct.unpack("!I", payload[:4])[0], payload[4:]


class FramedTests(unittest.TestCase):
    def cli(self, server, *args):
        return subprocess.Popen(
            [
                sys.executable,
                wifidebug.__file__,
                "uart",
                "--host",
                "127.0.0.1",
                "--port",
                str(server.port),
                *args,
            ],
            env=dict(os.environ, STM32_ROM_TOKEN="fixture"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_stdio_binary_pause_resume_same_connection(self):
        paused, resume = threading.Event(), threading.Event()
        binary = bytes(range(256)) * 8

        def handler(conn, req, closed):
            self.assertEqual(req, dict(token="fixture", op="uart-framed", baud=115200))
            conn.sendall(
                b'{"ok":true,"framed":1,"epoch":1,"paused":false}\n' + data_frame(1, b"BEGIN")
            )
            received = b""
            while len(received) < len(binary):
                epoch, chunk = read_frame(conn)
                self.assertEqual(epoch, 1)
                received += chunk
            self.assertEqual(received, binary)
            conn.sendall(status_frame("paused", 2) + data_frame(1, b"STALE"))
            paused.set()
            self.assertTrue(resume.wait(2))
            conn.settimeout(0.15)
            with self.assertRaises(socket.timeout):
                conn.recv(1)  # paused input must never reach TCP
            conn.settimeout(3)
            wire = (
                status_frame("resumed", 3)
                + data_frame(2, b"STALE2")
                + data_frame(3, binary[:1024])
                + data_frame(3, binary[1024:])
            )
            for index in range(0, len(wire), 13):
                conn.sendall(wire[index : index + 13])
            epoch, chunk = read_frame(conn)
            self.assertEqual((epoch, chunk), (3, b"AFTER"))

        server = Server(handler)
        proc = self.cli(server, "--stdio")
        try:
            proc.stdin.write(binary)
            proc.stdin.flush()
            self.assertTrue(paused.wait(2))
            time.sleep(0.1)
            rejected = b"DROP\x00" * 1000
            proc.stdin.write(rejected)
            proc.stdin.flush()
            time.sleep(0.15)
            resume.set()
            time.sleep(0.35)
            proc.stdin.write(b"AFTER")
            proc.stdin.flush()
            stdout, stderr = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(stdout, b"BEGIN" + binary)
            import re

            self.assertEqual(
                sum(map(int, re.findall(rb"rejected (\d+) input bytes", stderr))), len(rejected)
            )
            self.assertIn(b"resumed epoch=3", stderr)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        server.check()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux PTY")
    def test_pty_survives_pause_resume_and_sigint(self):
        import signal
        import select
        import tempfile

        paused, resume, finished = threading.Event(), threading.Event(), threading.Event()

        def handler(conn, req, closed):
            conn.sendall(b'{"ok":true,"framed":1,"epoch":5,"paused":false}\n')
            self.assertEqual(read_frame(conn), (5, b"PING\x00"))
            conn.sendall(data_frame(5, b"PONG\x00") + status_frame("paused", 6))
            paused.set()
            self.assertTrue(resume.wait(2))
            conn.settimeout(0.1)
            with self.assertRaises(socket.timeout):
                conn.recv(1)
            conn.settimeout(3)
            conn.sendall(
                status_frame("resumed", 7) + data_frame(5, b"STALE") + data_frame(7, b"READY")
            )
            self.assertEqual(read_frame(conn), (7, b"NEW"))
            finished.set()
            self.assertEqual(conn.recv(1), b"")

        server = Server(handler)
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as temp:
            link = Path(temp) / "uart"
            proc = self.cli(server, "--pty", "--pty-link", str(link))
            fd = None
            try:
                deadline = time.monotonic() + 2
                while not link.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(link.exists())
                fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
                os.write(fd, b"PING\x00")
                self.assertTrue(paused.wait(2))
                self.assertTrue(select.select([fd], [], [], 2)[0])
                self.assertEqual(os.read(fd, 100), b"PONG\x00")
                time.sleep(0.1)
                os.write(fd, b"DROP")
                time.sleep(0.15)
                resume.set()
                self.assertTrue(select.select([fd], [], [], 2)[0])
                self.assertEqual(os.read(fd, 100), b"READY")
                os.write(fd, b"NEW")
                self.assertTrue(finished.wait(2))
                proc.send_signal(signal.SIGINT)
                out, err = proc.communicate(timeout=2)
                self.assertEqual(proc.returncode, 130, err)
                self.assertEqual(out, b"")
                self.assertIn(b"rejected 4 input bytes", err)
                self.assertFalse(link.is_symlink())
            finally:
                if fd is not None:
                    os.close(fd)
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
        server.check()

    def test_parser_validation_and_coalescing(self):
        import struct

        for bad in (b"\x03\x00\x00", b"\x01\x04\x05", b"\x01\x00\x03", b"\x02\x00\x00"):
            with self.assertRaises(wifidebug.StreamError):
                list(wifidebug.FrameParser().feed(bad))
        for bad in (b"{", b"[]", b'{"event":"paused","epoch":true}', b'{"event":"bad","epoch":1}'):
            session = wifidebug.FramedUART(None, dict(framed=1, epoch=0, paused=False))
            with self.assertRaises(wifidebug.StreamError):
                session.receive(wifidebug.record(2, bad), lambda data: None)
        parser = wifidebug.FrameParser()
        wire = data_frame(1, bytes(range(256))) * 4
        result = []
        for byte in wire:
            result.extend(parser.feed(bytes([byte])))
        self.assertEqual(len(result), 4)
        self.assertFalse(parser.buffer)

    def test_windows_binary_mode_and_bat_encoding(self):
        from unittest import mock

        fake = mock.Mock()
        with (
            mock.patch.object(wifidebug.os, "name", "nt"),
            mock.patch.dict(sys.modules, {"msvcrt": fake}),
            mock.patch.object(wifidebug.os, "O_BINARY", 0x8000, create=True),
            mock.patch.object(wifidebug.sys, "stdin") as stdin,
            mock.patch.object(wifidebug.sys, "stdout") as stdout,
        ):
            stdin.fileno.return_value = 8
            stdout.fileno.return_value = 9
            wifidebug.binary_stdio()
            self.assertEqual(
                fake.setmode.call_args_list, [mock.call(8, 0x8000), mock.call(9, 0x8000)]
            )
        for name in ("9-STM32串口.bat", "10-STM32进程串口.bat"):
            data = Path(wifidebug.__file__).with_name(name).read_bytes()
            data.decode("ascii")
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        process_bat = (
            Path(wifidebug.__file__).with_name("10-STM32进程串口.bat").read_bytes().lower()
        )
        self.assertNotIn(b"pause", process_bat)
        self.assertNotIn(b"set /p", process_bat)
        self.assertIn(b"--stdio", process_bat)

    def test_concurrent_writers_chunking_and_initial_pause(self):
        import collections

        left, right = socket.socketpair()
        session = wifidebug.FramedUART(
            left, dict(framed=1, epoch=4, paused=True), lambda text: None
        )
        try:
            session.send(b"DROP")
            session.receive(status_frame("resumed", 5), lambda data: None)
            writers = [
                threading.Thread(target=session.send, args=(bytes([i]) * 2500,)) for i in (11, 22)
            ]
            for writer in writers:
                writer.start()
            results = collections.Counter()
            for _ in range(6):
                epoch, chunk = read_frame(right)
                self.assertEqual(epoch, 5)
                self.assertEqual(len(set(chunk)), 1)
                results[chunk[0]] += len(chunk)
            for writer in writers:
                writer.join(1)
                self.assertFalse(writer.is_alive())
            self.assertEqual(results, {11: 2500, 22: 2500})
        finally:
            left.close()
            right.close()

    def test_truncated_frame_closes_cli(self):
        def handler(conn, req, closed):
            conn.sendall(b'{"ok":true,"framed":1,"epoch":1,"paused":false}\n\x01\x00\x05\x00')

        server = Server(handler)
        proc = self.cli(server, "--stdio")
        out, err = proc.communicate(timeout=3)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(out, b"")
        self.assertIn(b"Truncated UART frame", err)
        server.check()

    def test_framed_exec_token_file(self):
        import tempfile

        def handler(conn, req, closed):
            self.assertEqual(req["token"], "private-file-token")
            self.assertEqual(req["op"], "uart-framed")
            conn.sendall(b'{"ok":true,"framed":1,"epoch":10,"paused":false}\n')
            self.assertEqual(read_frame(conn), (10, b"ping\n"))
            conn.sendall(data_frame(10, b"PONG\x00"))

        server = Server(handler)
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as temp:
            token = Path(temp) / "token.txt"
            token.write_text("private-file-token")
            result = subprocess.run(
                [
                    sys.executable,
                    wifidebug.__file__,
                    "--token-file",
                    str(token),
                    "exec",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(server.port),
                    "--send",
                    "ping\n",
                ],
                capture_output=True,
                timeout=3,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"PONG\x00")
        server.check()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux PTY")
    def test_pty_refuses_existing_link(self):
        import tempfile

        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as temp:
            link = Path(temp) / "existing"
            link.write_bytes(b"KEEP")
            with self.assertRaises(FileExistsError):
                with wifidebug.virtual_pty(link):
                    pass
            self.assertEqual(link.read_bytes(), b"KEEP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
