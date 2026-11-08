"""Registry contracts: no SDK, network or hardware needed."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rmb", ROOT / "tools/rmb.py")
rmb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rmb)


class Registry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = copy.deepcopy(rmb.discover(ROOT, "backends")["esp-idf"][0])
        self.board = copy.deepcopy(rmb.discover(ROOT, "boards")["esp32c3-stm32f103"][0])
        for kind, item, filename in [
            ("backends", self.backend, "backend.json"),
            ("boards", self.board, "board.json"),
        ]:
            path = self.root / kind / item["id"] / filename
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(item))
        (self.root / self.backend["implementation"]).mkdir()
        (self.root / "backends/esp-idf" / self.backend["driver"]).write_text("")
        for default in self.board["defaults"]:
            path = self.root / default
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def check_invalid(self, kind, item):
        filename = "backend.json" if kind == "backends" else "board.json"
        ident = "esp-idf" if kind == "backends" else "esp32c3-stm32f103"
        (self.root / kind / ident / filename).write_text(json.dumps(item))
        with self.assertRaises(ValueError):
            rmb.discover(self.root, kind)

    def test_real_backend_and_board(self):
        self.assertEqual(list(rmb.discover(ROOT, "backends")), ["esp-idf"])
        rmb.compatible(self.backend, self.board)
        self.assertEqual(
            self.board["pins"],
            dict(
                tx=3, rx=4, boot0=5, nrst=6, boot1=-1, spi_sclk=7, spi_mosi=1, spi_miso=0, spi_cs=10
            ),
        )

    def test_missing_required_fields(self):
        for kind, original, fields in [
            (
                "backends",
                self.backend,
                [
                    "schema_version",
                    "id",
                    "description",
                    "implementation",
                    "driver",
                    "capabilities",
                    "requirements",
                ],
            ),
            (
                "boards",
                self.board,
                ["schema_version", "id", "backend", "target", "defaults", "capabilities", "pins"],
            ),
        ]:
            for field in fields:
                with self.subTest(kind=kind, field=field):
                    item = copy.deepcopy(original)
                    del item[field]
                    self.check_invalid(kind, item)

    def test_invalid_types_and_values(self):
        for kind, original, mutations in [
            (
                "backends",
                self.backend,
                {
                    "schema_version": [True, "1", 2],
                    "id": [1, [], "../bad", "wrong-id"],
                    "capabilities": [None, {}, ["uart", "uart"], ["unknown"], [{}]],
                    "implementation": [None, [], "../escape", "/absolute", "missing"],
                    "driver": [{}, "../escape", "/absolute", "missing"],
                    "requirements": [
                        None,
                        [],
                        {},
                        {"sdk": {}},
                        {
                            "sdk": {"name": "SDK", "version": "1", "environment": "SDK"},
                            "toolchain": [],
                            "python": ">=3.10",
                        },
                    ],
                },
            ),
            (
                "boards",
                self.board,
                {
                    "backend": [None, {}, "../bad"],
                    "target": ["", 3],
                    "defaults": [None, [], [{}], ["../escape"], ["/absolute"], ["missing"]],
                    "pins": [None, [], {"tx": True}, {"tx": -2}, {"tx": "1"}],
                    "capabilities": [["unknown"], [[]], ["uart", "uart"]],
                },
            ),
        ]:
            for field, values in mutations.items():
                for value in values:
                    with self.subTest(kind=kind, field=field, value=value):
                        item = copy.deepcopy(original)
                        item[field] = value
                        self.check_invalid(kind, item)
        for value in [None, [], 1, "text"]:
            self.check_invalid("backends", value)

    def test_nested_requirements(self):
        for field in ("name", "version", "environment"):
            for value in (None, "", 3, []):
                item = copy.deepcopy(self.backend)
                item["requirements"]["sdk"][field] = value
                self.check_invalid("backends", item)
        for field, values in [("toolchain", [None, "gcc", [], [3]]), ("python", [None, "", []])]:
            for value in values:
                item = copy.deepcopy(self.backend)
                item["requirements"][field] = value
                self.check_invalid("backends", item)

    def test_directory_extension_without_dispatch_branch(self):
        path = self.root / "backends/new-port"
        path.mkdir()
        item = copy.deepcopy(self.backend)
        item["id"] = "new-port"
        (path / item["driver"]).write_text("")
        (path / "backend.json").write_text(json.dumps(item))
        self.assertIn("new-port", rmb.discover(self.root, "backends"))

    def test_traversal_and_symlink_escape(self):
        for value in ("../escape", "/absolute", "foo/../bar", None, 3):
            with self.assertRaises(ValueError):
                rmb.local(self.root, value)
        (self.root / "escape").symlink_to(ROOT, target_is_directory=True)
        with self.assertRaises(ValueError):
            rmb.local(self.root, "escape/tools/rmb.py")
        (self.root / "backends/foreign").symlink_to(
            ROOT / "backends/esp-idf", target_is_directory=True
        )
        with self.assertRaises(ValueError):
            rmb.discover(self.root, "backends")

    def test_incompatible_capabilities_and_backend(self):
        for field, value in [("backend", "other"), ("capabilities", ["spi"])]:
            item = copy.deepcopy(self.board)
            item[field] = value
            with self.assertRaises(ValueError):
                rmb.compatible(self.backend, item)

    def test_cli_unknown_backend(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/rmb.py"), "configure", "--backend", "missing"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_driver_rejects_metadata_wiring_before_sdk(self):
        board = copy.deepcopy(self.board)
        board["pins"]["tx"] = 4
        path = self.root / "boards/esp32c3-stm32f103/board.json"
        path.write_text(json.dumps(board))
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "backends/esp-idf/build.py"),
                "configure",
                "--root",
                str(ROOT),
                "--backend",
                str(ROOT / "backends/esp-idf/backend.json"),
                "--board",
                str(path),
                "--build-dir",
                str(self.root / "build"),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "IDF_PATH": ""},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixed esp32c3-stm32f103", result.stderr)
        self.assertFalse((self.root / "build").exists())

    def test_malformed_json(self):
        (self.root / "backends/esp-idf/backend.json").write_text("{")
        with self.assertRaises(ValueError):
            rmb.discover(self.root, "backends")


if __name__ == "__main__":
    unittest.main()
