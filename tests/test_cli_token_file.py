"""Credential-file CLI behavior; no device access or production credentials."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

CLI = Path(__file__).resolve().parents[1] / "tools" / "romctl.py"
spec = importlib.util.spec_from_file_location("romctl_token_file", CLI)
assert spec is not None and spec.loader is not None
romctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(romctl)


class TokenFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_file = Path(self.directory.name) / "private-token.txt"

    def test_explicit_file_overrides_environment_without_prompt_or_echo(self):
        token = "native-token-file-only"
        self.token_file.write_text(token + "\n")
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"STM32_ROM_TOKEN": "wrong-environment-token"}),
            patch.object(
                romctl.getpass, "getpass", side_effect=AssertionError("unexpected prompt")
            ),
            patch.object(romctl, "control", return_value={"ok": True}) as control,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                romctl.main(
                    ["status", "--host", "127.0.0.1", "--token-file", str(self.token_file)]
                ),
                0,
            )
        self.assertEqual(control.call_args.args[2], token)
        self.assertNotIn(token, output.getvalue())
        self.assertEqual(json.loads(output.getvalue()), {"ok": True})

    def test_bad_explicit_file_never_falls_back_or_contacts_device(self):
        for contents in (None, b"", b"one\ntwo\n", b"bad\0value", b"\xff"):
            with self.subTest(contents=contents):
                if self.token_file.exists():
                    self.token_file.unlink()
                if contents is not None:
                    self.token_file.write_bytes(contents)
                with (
                    patch.dict(os.environ, {"STM32_ROM_TOKEN": "valid-fallback-not-allowed"}),
                    patch.object(
                        romctl.getpass, "getpass", side_effect=AssertionError("unexpected prompt")
                    ),
                    patch.object(romctl, "control") as control,
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as error:
                        romctl.main(
                            ["status", "--host", "127.0.0.1", "--token-file", str(self.token_file)]
                        )
                self.assertEqual(error.exception.code, 1)
                control.assert_not_called()

    def test_environment_and_interactive_compatibility(self):
        with patch.dict(os.environ, {"CUSTOM_TOKEN_ENV": "public-native-env"}):
            self.assertEqual(romctl.load_token(token_env="CUSTOM_TOKEN_ENV"), "public-native-env")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(romctl.getpass, "getpass", return_value="public-native-prompt"),
        ):
            self.assertEqual(romctl.load_token(), "public-native-prompt")

    def test_discovery_does_not_require_credentials(self):
        with (
            patch.object(romctl, "discover", return_value=[]),
            patch.object(
                romctl, "load_token", side_effect=AssertionError("discovery is unauthenticated")
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                romctl.main(
                    ["discover", "--discovery-timeout", "0", "--token-file", str(self.token_file)]
                ),
                0,
            )

    def test_probe_help_matches_server_fixed_baud(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            romctl.main(["--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("115200", output.getvalue())
        self.assertNotIn("9600", output.getvalue())


if __name__ == "__main__":
    unittest.main()
