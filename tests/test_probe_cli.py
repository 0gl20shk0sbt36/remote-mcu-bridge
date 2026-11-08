"""Probe wire/CLI contract over real localhost TCP (not ESP/UART hardware)."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

CLI = Path(__file__).resolve().parents[1] / "tools" / "romctl.py"
spec = importlib.util.spec_from_file_location("romctl_probe", CLI)
assert spec is not None and spec.loader is not None
romctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(romctl)
TOKEN = "local-test-only-!&-token"


def report(probe_ok=True):
    # Diagnostic completion and target success are deliberately independent.
    return dict(
        ok=True,
        probe_ok=probe_ok,
        stage="get" if probe_ok else "sync",
        phase="done" if probe_ok else "ack",
        ack=0x79 if probe_ok else -1,
        commands=3,
        baud=9600,
        tx=7,
        rx=24,
        version=0x10,
        get_version=0x10 if probe_ok else 0x31,
        option1=0xA5,
        option2=0x5A,
        version_queried=True,
        version_mismatch=not probe_ok,
        version_zero=False,
        first_byte_us=123 if probe_ok else -1,
        counters=dict(sync_attempts=1, timeouts=0 if probe_ok else 1),
        future_field=dict(detail="preserve arbitrary server fields"),
    )


@contextlib.contextmanager
def server(response, expected_token=TOKEN):
    """One authenticated exchange; capture thread errors in the caller."""
    errors, requests = [], []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        port = listener.getsockname()[1]

        def serve():
            try:
                with listener.accept()[0] as conn:
                    conn.settimeout(3)
                    with conn.makefile("rb") as stream:
                        raw = stream.readline(4097)
                        if not raw.endswith(b"\n"):
                            raise AssertionError("missing JSON newline")
                        request = json.loads(raw)
                        requests.append(request)
                        # Exact contract: no baud override, size, CRC or image.
                        if request.get("op") != "probe" or set(request) != {"op", "token"}:
                            raise AssertionError("unexpected probe request fields")
                        reply = (
                            response
                            if request["token"] == expected_token
                            else dict(ok=False, error="unauthorized")
                        )
                        wire = (json.dumps(reply) + "\n").encode()
                        # Exercise framing through multiple writes, not a mock socket.
                        conn.sendall(wire[:7])
                        conn.sendall(wire[7:])
                        if stream.read(1) != b"":
                            raise AssertionError("probe must not send image bytes")
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield port, requests
        finally:
            thread.join(4)
            if thread.is_alive():
                raise AssertionError("probe server did not finish")
            if errors:
                raise errors[0]


class ProbeTests(unittest.TestCase):
    def cli(self, port, *extra, token=TOKEN):
        env = os.environ.copy()
        env["STM32_ROM_TOKEN"] = token
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--device",
                "f103c8",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--timeout",
                "2",
                "probe",
                *extra,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_authenticated_control_real_socket(self):
        expected = report()
        with server(expected) as (port, requests):
            self.assertEqual(
                romctl.control("127.0.0.1", port, TOKEN, "probe", timeout=2, fragment=1), expected
            )
        self.assertEqual(requests, [dict(op="probe", token=TOKEN)])

    def test_cli_preserves_success_and_failed_rom_reports(self):
        for probe_ok in (True, False):
            with self.subTest(probe_ok=probe_ok):
                expected = report(probe_ok)
                with server(expected) as (port, requests):
                    proc = self.cli(port)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(proc.stdout), expected)
                self.assertEqual(len(requests), 1)
                self.assertEqual(proc.stderr, "")
                self.assertNotIn(TOKEN, proc.stdout + proc.stderr)

    def test_cli_auth_failure(self):
        with server(report()) as (port, _):
            proc = self.cli(port, token="wrong-local-test-token")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("unauthorized", proc.stderr)
        self.assertNotIn("wrong-local-test-token", proc.stderr)

    def test_cli_server_failure(self):
        with server(dict(ok=False, error="probe unavailable")) as (port, _):
            proc = self.cli(port)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("probe unavailable", proc.stderr)

    def test_cli_rejects_image_before_discovery_auth_or_connection(self):
        with (
            patch.object(romctl, "discover") as discover,
            patch.object(romctl.getpass, "getpass") as prompt,
            patch.object(romctl, "control") as control,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            with self.assertRaises(SystemExit) as caught:
                romctl.main(["probe", "must-not-read.bin"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("probe does not accept an image", stderr.getvalue())
        discover.assert_not_called()
        prompt.assert_not_called()
        control.assert_not_called()

    def test_cli_image_rejection_subprocess(self):
        proc = self.cli(1, "must-not-read.bin")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("probe does not accept an image", proc.stderr)

    def test_control_rejects_payload_before_socket(self):
        with patch.object(romctl.socket, "create_connection") as connect:
            for payload in (b"", b"not-an-image"):
                with self.assertRaisesRegex(ValueError, "probe does not accept an image"):
                    romctl.control("127.0.0.1", 1, TOKEN, "probe", payload)
        connect.assert_not_called()

    def test_probe_auto_discovery_preserves_multi_nic_options(self):
        with (
            patch.dict(os.environ, {"STM32_ROM_TOKEN": TOKEN}),
            patch.object(
                romctl, "discover", return_value=[dict(host="127.0.0.1", port=3456)]
            ) as discover,
            patch.object(romctl, "control", return_value=report(False)) as control,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            self.assertEqual(
                romctl.main(
                    [
                        "--device",
                        "f103c8",
                        "probe",
                        "--interface",
                        "192.0.2.2",
                        "--interface",
                        "203.0.113.2",
                        "--discovery-timeout",
                        ".25",
                        "--passive-only",
                    ]
                ),
                0,
            )
        discover.assert_called_once_with(
            "255.255.255.255",
            3334,
            0.25,
            interfaces=["192.0.2.2", "203.0.113.2"],
            passive_only=True,
        )
        control.assert_called_once_with("127.0.0.1", 3456, TOKEN, "probe", None, 120)
        self.assertEqual(json.loads(stdout.getvalue()), report(False))

    def test_no_baud_override(self):
        proc = self.cli(1, "--baud", "115200")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unrecognized arguments", proc.stderr)


if __name__ == "__main__":
    unittest.main()
