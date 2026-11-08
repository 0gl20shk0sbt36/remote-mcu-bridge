"""Host checks: execute actual C digest parser; audit supervisor contracts.
Not an ESP32 emulator; no hardware/watchdog/flash claims.
"""

import ctypes
import csv
import os
from pathlib import Path
import re
import random
import subprocess
import sys
import tempfile
import unittest


def at(text, literal):
    """Locate a code anchor, tolerating formatter whitespace around tokens."""
    pattern = re.escape(literal)
    for token, flexible in (
        (r"\ ", r"\s*"),
        (",", r"\s*,\s*"),
        (r"\(", r"\(\s*"),
        (r"\)", r"\s*\)"),
        (r"\{", r"\{\s*"),
        (r"\}", r"\s*\}"),
        (";", r"\s*;"),
        ("=", r"\s*=\s*"),
    ):
        pattern = pattern.replace(token, flexible)
    match = re.search(pattern, text)
    if not match:
        raise ValueError("missing anchor: " + literal)
    return match.start()


def flat(text):
    """Compare C token runs without depending on formatting whitespace."""
    return re.sub(r"\s+", "", text)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "main/supervisor.inc").read_text()
SERVICE = (ROOT / "main/service.c").read_text()
BUSINESS = (ROOT / "module/business.c").read_text()


class SupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        start = at(SOURCE, "static bool supervisor_hex(")
        end = SOURCE.index("static void supervisor_serve(", start)
        c = "#include <stdint.h>\n#include <stdbool.h>\n#include <string.h>\n"
        c += SOURCE[start:end].replace("static bool supervisor_hex", "bool supervisor_hex")
        p = Path(cls.tmp.name)
        (p / "parser.c").write_text(c)
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-shared",
                "-fPIC",
                str(p / "parser.c"),
                "-o",
                str(p / "parser.so"),
            ],
            check=True,
        )
        cls.lib = ctypes.CDLL(str(p / "parser.so"))
        cls.lib.supervisor_hex.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint8)]
        cls.lib.supervisor_hex.restype = ctypes.c_bool

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_digest_roundtrip(self):
        rng = random.Random(15)
        for _ in range(1000):
            data = bytes(rng.randrange(256) for _ in range(32))
            for text in (data.hex(), data.hex().upper()):
                out = (ctypes.c_uint8 * 32)()
                self.assertTrue(self.lib.supervisor_hex(text.encode(), out))
                self.assertEqual(bytes(out), data)

    def test_digest_malformed(self):
        out = (ctypes.c_uint8 * 32)()
        for size in range(130):
            if size != 64:
                self.assertFalse(self.lib.supervisor_hex(b"0" * size, out))
        for pos in range(64):
            for bad in (b"g", b"/", b":", b" ", b"\n", b"\xff"):
                text = bytearray(b"0" * 64)
                text[pos] = bad[0]
                self.assertFalse(self.lib.supervisor_hex(bytes(text), out))
        self.assertFalse(self.lib.supervisor_hex(None, out))

    def test_partition(self):
        rows = {
            r[0]: r
            for r in csv.reader((ROOT / "partitions.csv").read_text().splitlines())
            if r and not r[0].startswith("#")
        }
        self.assertEqual(rows["business"][1:5], ["data", "0x41", "0x220000", "0x100000"])
        self.assertEqual(rows["staging"][3:5], ["0x110000", "0x110000"])
        self.assertEqual(rows["factory"][3:5], ["0x10000", "0x100000"])

    def test_resident_contracts(self):
        serve = SOURCE[
            at(SOURCE, "static void supervisor_serve(") : at(
                SOURCE, "static void supervisor_management("
            )
        ]
        self.assertLess(
            at(serve, "authenticated(token->valuestring)"),
            at(serve, "module_runtime_install_begin(size)"),
        )
        self.assertLess(
            at(serve, "supervisor_active || module_runtime_busy()"),
            at(serve, "module_runtime_install_begin(size)"),
        )
        self.assertNotIn(flat("control_acquire"), flat(serve))
        self.assertLess(
            at(serve, "module_runtime_install_begin(size)"),
            at(serve, "module_runtime_install_write("),
        )
        self.assertLess(
            at(serve, "module_runtime_install_write("), at(serve, "module_runtime_install_finish(")
        )
        self.assertIn(flat("seen!=15"), flat(serve))
        self.assertIn(flat("seen!=3"), flat(serve))
        self.assertIn(flat("seen!=19"), flat(serve))
        self.assertIn(flat("service_fault_ack(seq)"), flat(serve))
        self.assertIn(flat("UPLOAD_US"), flat(serve))
        self.assertIn(flat("module_runtime_invalidate()"), flat(serve))
        self.assertIn(flat("RTC_NOINIT_ATTR"), flat(SOURCE))
        self.assertIn(flat("rmb_supervisor_record_write(&supervisor_rtc"), flat(SOURCE))
        self.assertIn(flat(".timeout_ms=20000"), flat(SOURCE))
        self.assertIn(
            flat("if(module_allowed_this_boot && !supervisor_fault && supervisor_start())"),
            flat(SOURCE),
        )
        self.assertIn(flat("xSemaphoreTake(job->done,portMAX_DELAY)"), flat(SOURCE))
        self.assertIn(flat(".trigger_panic=true"), flat(SOURCE))
        self.assertIn(flat("for(;;) vTaskDelay(1)"), flat(SOURCE))
        self.assertIn(flat("supervisor_updating=false"), flat(SOURCE))
        self.assertNotIn(flat("control_release"), flat(SOURCE))
        for call in ("rom_validate(", "rom_download_traced(", "rom_probe(", "rom_restart("):
            self.assertNotIn("= " + call, SERVICE)
        self.assertIn(flat("if (remaining > 250000)"), flat(BUSINESS))
        self.assertIn(flat("service_status(fd)"), flat(BUSINESS))
        self.assertNotIn(flat("supervisor_status(fd)"), flat(BUSINESS))

    def test_xip_activation_contracts(self):
        setup = SOURCE[at(SOURCE, "static void supervisor_init(void)") :]
        self.assertIn(flat("module_runtime_prepare()"), flat(SERVICE))
        self.assertLess(at(SERVICE, "module_runtime_prepare()"), at(SERVICE, "nvs_flash_init()"))
        self.assertLess(
            at(SERVICE, "module_runtime_prepare()"), at(SERVICE, "esp_wifi_init(&init)")
        )
        self.assertNotIn(flat("module_runtime_init()"), flat(setup))
        self.assertNotIn(flat("supervisor_submit("), flat(setup))
        manager = SOURCE[
            at(SOURCE, "static void supervisor_management(") : at(
                SOURCE, "static void supervisor_monitor("
            )
        ]
        self.assertLess(at(manager, "listen(server,2)"), at(manager, "supervisor_start()"))
        main = SERVICE[at(SERVICE, "void app_main(void)") :]
        self.assertLess(
            at(main, "supervisor_init()"), at(main, "xTaskCreate(supervisor_management")
        )
        start = SOURCE[
            at(SOURCE, "static int supervisor_start(") : at(
                SOURCE, "static void supervisor_status("
            )
        ]
        self.assertIn(flat("static supervisor_job init_job"), flat(start))
        self.assertNotIn(flat("xSemaphoreTake"), flat(start))
        self.assertIn(flat("if(job->done) xSemaphoreGive(job->done)"), flat(SOURCE))
        for field in ("text_address", "data_address", "module_abi", "reboot_required"):
            self.assertIn(field, SOURCE)

    def test_cli_enter_update(self):
        import json
        import socket
        import threading

        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            observed = []

            def serve():
                conn, _ = server.accept()
                with conn, conn.makefile("rb") as stream:
                    observed.append(json.loads(stream.readline()))
                    conn.sendall(b'{"ok":true,"reboot_required":true}\n')

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            env = dict(os.environ, STM32_ROM_TOKEN="native-cli-test-token")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT.parent / "tools/module_ctl.py"),
                    "enter-update",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(server.getsockname()[1]),
                ],
                env=env,
                text=True,
                capture_output=True,
                timeout=5,
            )
            worker.join(timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"ok": True, "reboot_required": True})
            self.assertEqual(observed, [{"token": "native-cli-test-token", "op": "enter-update"}])

    def test_actual_hung_entry_management(self):
        subprocess.run(
            [sys.executable, str(ROOT / "tests/test_supervisor_startup.py")], check=True, timeout=15
        )


if __name__ == "__main__":
    unittest.main()
