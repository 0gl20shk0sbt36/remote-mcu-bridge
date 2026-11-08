"""Compile-time configuration: single file, redacted credentials overlay.

These tests never read a real credential file and never require ESP-IDF: they
exercise the pure composition/validation helpers plus the CLI's argument
surface. Values used here are obviously fake.
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "backends/esp-idf/config_defaults.py"
spec = importlib.util.spec_from_file_location("config_defaults", MODULE)
assert spec is not None and spec.loader is not None
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)

BUILD = ROOT / "backends/esp-idf/build.py"


def settings(**overrides):
    """Minimal valid sdkconfig mapping for the enabled profile."""
    values = {
        "CONFIG_RMB_ENABLE": "y",
        "CONFIG_ESP_INT_WDT": "y",
        "CONFIG_ESP_TASK_WDT_EN": "y",
        "CONFIG_ESP_TASK_WDT_PANIC": "y",
        "CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT": "y",
        "CONFIG_RMB_WIFI_SSID": config.kconfig_string("lab-network"),
        "CONFIG_RMB_WIFI_PASSWORD": config.kconfig_string("lab-password-1"),
        "CONFIG_RMB_TOKEN": config.kconfig_string("native-test-token-0123456789"),
        "CONFIG_RMB_LISTENER_STACK_BYTES": "12288",
        "CONFIG_ROM_SERVICE_DEVICE_F103C8": "y",
    }
    values.update(overrides)
    return values


class SingleConfigurationFileTests(unittest.TestCase):
    def test_board_registry_points_at_exactly_one_file(self):
        board = json.loads((ROOT / "boards/esp32c3-stm32f103/board.json").read_text())
        self.assertEqual(board["defaults"], ["config/build.defaults"])
        text = (ROOT / "config/build.defaults").read_text()
        # The one file must carry every compile-time decision we relied on.
        for required in (
            'CONFIG_IDF_TARGET="esp32c3"',
            "CONFIG_PARTITION_TABLE_CUSTOM=y",
            "CONFIG_ESP_INT_WDT=y",
            "CONFIG_ESP_TASK_WDT_PANIC=y",
            "CONFIG_RMB_ENABLE=y",
            "CONFIG_RMB_PORT=3333",
            "CONFIG_RMB_DISCOVERY_PORT=3334",
            "CONFIG_RMB_LISTENER_STACK_BYTES=12288",
            "CONFIG_ROM_SERVICE_DEVICE_F103C8=y",
            "CONFIG_ROM_SERVICE_TX=3",
            "CONFIG_ROM_SERVICE_BOOT1=-1",
        ):
            self.assertIn(required, text)
        # Superseded split files must not come back.
        for gone in ("config/ci-enabled.defaults", "config/ci-disabled.defaults"):
            self.assertFalse((ROOT / gone).exists(), gone)
        self.assertFalse((ROOT / "esp32_rom/sdkconfig.defaults").exists())

    def test_no_absolute_paths_in_tracked_configuration(self):
        for relative in ("config/build.defaults", "boards/esp32c3-stm32f103/board.json"):
            text = (ROOT / relative).read_text()
            for marker in ("/home/", "/Users/", "C:\\", "D:\\"):
                self.assertNotIn(marker, text, relative)


class CredentialParsingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def write(self, text):
        path = Path(self.directory.name) / "wifi.pw"
        path.write_text(text)
        return path

    def test_positional_wifi_pw_shape(self):
        parsed = config.load_credentials(self.write("lab-network\nlab-password-1\n"))
        self.assertEqual(parsed, {"ssid": "lab-network", "password": "lab-password-1"})

    def test_positional_with_token_and_comments_and_blank_lines(self):
        parsed = config.load_credentials(
            self.write(
                "# bench credentials\n\nlab-network\nlab-password-1\nnative-test-token-0123456789\n"
            )
        )
        self.assertEqual(parsed["token"], "native-test-token-0123456789")

    def test_keyed_variants_and_unknown_keys_ignored(self):
        parsed = config.load_credentials(
            self.write(
                "wifi_ssid: lab network\npsk = lab-password-1\nnote=ignored\n"
                "lan_token=native-test-token-0123456789\n"
            )
        )
        self.assertEqual(parsed["ssid"], "lab network")
        self.assertEqual(parsed["password"], "lab-password-1")
        self.assertEqual(parsed["token"], "native-test-token-0123456789")

    def test_rejections_never_echo_the_value(self):
        cases = [
            ("", "no usable lines"),
            ("only-ssid\nshort\n", "8..63"),
            ("lab-network\nlab-password-1\ntiny\n", "16..128"),
            ("\n\n", "no usable lines"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                with self.assertRaises(config.ConfigError) as error:
                    config.load_credentials(self.write(text))
                self.assertIn(expected, str(error.exception))

    def test_missing_and_non_utf8_files(self):
        missing = Path(self.directory.name) / "absent.pw"
        with self.assertRaises(config.ConfigError) as error:
            config.load_credentials(missing)
        self.assertIn("does not exist", str(error.exception))
        bad = Path(self.directory.name) / "bad.pw"
        bad.write_bytes(b"lab-network\n\xff\xfe\n")
        with self.assertRaises(config.ConfigError) as error:
            config.load_credentials(bad)
        self.assertIn("UTF-8", str(error.exception))


class TokenFallbackTests(unittest.TestCase):
    def test_environment_token_fills_only_a_missing_field(self):
        base = {"ssid": "lab-network", "password": "lab-password-1"}
        completed = config.complete_credentials(base, "native-test-token-0123456789")
        self.assertEqual(completed["token"], "native-test-token-0123456789")
        # An explicit file token must win over the environment.
        explicit = config.complete_credentials(
            dict(base, token="file-token-0123456789"), "native-test-token-0123456789"
        )
        self.assertEqual(explicit["token"], "file-token-0123456789")
        # No fallback and no file token stays without a token; validation later refuses.
        self.assertNotIn("token", config.complete_credentials(base, None))
        for bad in ("short", "multi\nline-token-0123456789"):
            with self.subTest(bad=bad):
                with self.assertRaises(config.ConfigError):
                    config.complete_credentials(base, bad)


class OverlayTests(unittest.TestCase):
    def test_credentials_override_placeholders_when_applied_in_order(self):
        base = (ROOT / "config/build.defaults").read_text()
        overlay = "\n".join(config.credential_lines({"ssid": "lab network", "password": 'p"w'}))
        merged = base + "\n" + overlay
        values = {}
        for line in merged.splitlines():
            if line.startswith("CONFIG_") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value  # later assignment wins, like SDKCONFIG_DEFAULTS
        self.assertEqual(config.unquote(values["CONFIG_RMB_WIFI_SSID"]), "lab network")
        self.assertEqual(config.unquote(values["CONFIG_RMB_WIFI_PASSWORD"]), 'p"w')
        # The base file still holds the placeholder, so nothing secret was edited in.
        self.assertIn(config.PLACEHOLDER_SSID, base)

    def test_profile_overlay_only_disables_for_safe(self):
        self.assertEqual(config.profile_lines("compile-enabled"), [])
        self.assertIn("# CONFIG_RMB_ENABLE is not set", "\n".join(config.profile_lines("safe")))

    def test_validation_requires_real_credentials_for_enabled_profile(self):
        with self.assertRaises(config.ConfigError):
            config.validate(
                settings(CONFIG_RMB_WIFI_SSID=config.kconfig_string(config.PLACEHOLDER_SSID)),
                "compile-enabled",
            )
        with self.assertRaises(config.ConfigError):
            config.validate(
                settings(CONFIG_RMB_TOKEN=config.kconfig_string("short")), "compile-enabled"
            )
        with self.assertRaises(config.ConfigError):
            config.validate(settings(CONFIG_ESP_INT_WDT="n"), "compile-enabled")
        config.validate(settings(), "compile-enabled")

    def test_safe_profile_rejects_enabled_configuration(self):
        with self.assertRaises(config.ConfigError):
            config.validate(settings(), "safe")
        config.validate({"CONFIG_RMB_ENABLE": "n"}, "safe")

    def test_summary_is_redacted(self):
        summary = config.describe(settings())
        text = json.dumps(summary)
        self.assertNotIn("lab-network", text)
        self.assertNotIn("lab-password-1", text)
        self.assertNotIn("native-test-token", text)
        self.assertTrue(summary["ssid_configured"])
        self.assertFalse(summary["placeholder_network"])


class CliSurfaceTests(unittest.TestCase):
    def test_driver_and_registry_expose_credentials_flag(self):
        for command, script in (
            ("backends/esp-idf/build.py", BUILD),
            ("tools/rmb.py", ROOT / "tools/rmb.py"),
        ):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--credentials-file", result.stdout)
                self.assertIn("--user-config", result.stdout)

    def test_bad_credentials_fail_before_any_build_state(self):
        with tempfile.TemporaryDirectory() as directory:
            build_dir = Path(directory) / "out"
            missing = Path(directory) / "absent.pw"
            result = subprocess.run(
                [
                    sys.executable,
                    str(BUILD),
                    "configure",
                    "--root",
                    str(ROOT),
                    "--backend",
                    str(ROOT / "backends/esp-idf/backend.json"),
                    "--board",
                    str(ROOT / "boards/esp32c3-stm32f103/board.json"),
                    "--build-dir",
                    str(build_dir),
                    "--profile",
                    "compile-enabled",
                    "--credentials-file",
                    str(missing),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env=dict(os.environ, IDF_PATH=os.environ.get("IDF_PATH", "/nonexistent")),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not exist", result.stderr)
            self.assertFalse(build_dir.exists())


if __name__ == "__main__":
    unittest.main()
