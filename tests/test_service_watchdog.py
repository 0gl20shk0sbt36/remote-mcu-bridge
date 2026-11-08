#!/usr/bin/env python3
"""Compile and run the actual resident watchdog with native platform shims."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProductionWatchdog(unittest.TestCase):
    def test_native_asan_ubsan(self):
        with tempfile.TemporaryDirectory(prefix="rmb-watchdog-") as build:
            binary = Path(build) / "watchdog-native"
            command = [
                os.environ.get("CC", "cc"),
                "-std=c11",
                "-g",
                "-O1",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fno-omit-frame-pointer",
                "-fsanitize=address,undefined",
                "-fno-pie",
                "-no-pie",
                "-I" + str(ROOT / "include"),
                "-I" + str(ROOT / "esp32_rom" / "tests"),
                '-DRMB_PORT_PLATFORM_HEADER="port_native.h"',
                str(ROOT / "tests/watchdog_native.c"),
                "-o",
                str(binary),
            ]
            compiled = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            env = dict(
                os.environ,
                ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
                UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
            )
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, env=env, timeout=30
            )
            print(result.stdout, end="")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("ALL production watchdog tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
