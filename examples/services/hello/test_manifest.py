#!/usr/bin/env python3
"""Offline builder contract tests; no target, SDK, network or toolchain required."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "module_builder", ROOT / "esp32_rom/module/build_module.py"
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            dir=os.environ.get("TMPDIR", str(Path.home() / "tmp"))
        )
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "hello.c").write_text("int module_entry(void) { return 0; }\n")
        self.path = self.root / "module.json"
        self.manifest: dict[str, Any] = dict(
            name="hello-1", sources=["hello.c"], drivers=[], defines={"SERVICE_VARIANT": 1}
        )

    def load(self):
        self.path.write_text(json.dumps(self.manifest))
        return builder.load_manifest(self.path)

    def test_valid(self):
        for value in (1, 2, -1, 0):
            self.manifest["defines"]["SERVICE_VARIANT"] = value
            manifest, sources = self.load()
            self.assertEqual(manifest, self.manifest)
            self.assertEqual(sources, [self.root / "hello.c"])

    def test_real_hello_manifests(self):
        for variant, name in ((1, "A"), (2, "B")):
            manifest, sources = builder.load_manifest(Path(__file__).parent / (name + ".json"))
            self.assertEqual(manifest["defines"], {"SERVICE_VARIANT": variant})
            self.assertEqual([p.name for p in sources], ["service.c", "platform_idf.c"])
            self.assertEqual(manifest["drivers"], [])

    def test_reject_names(self):
        for name in ("", "../escape", "a b", "a;cmd", "a\n", "é", 1, None):
            with self.subTest(name=name):
                self.manifest["name"] = name
                with self.assertRaises(ValueError):
                    self.load()

    def test_reject_sources(self):
        (self.root / "not.c").mkdir()
        (self.root / "header.h").write_text("")
        for sources in (
            [],
            "hello.c",
            [1],
            ["/etc/passwd"],
            ["../escape.c"],
            ["not.c"],
            ["header.h"],
            ["hello.c", "./hello.c"],
        ):
            with self.subTest(sources=sources):
                self.manifest["sources"] = sources
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.load()

    def test_symlink_escape(self):
        outside = self.root.parent / (self.root.name + "-outside.c")
        outside.write_text("")
        self.addCleanup(outside.unlink)
        (self.root / "escape.c").symlink_to(outside)
        self.manifest["sources"] = ["escape.c"]
        with self.assertRaises(ValueError):
            self.load()

    def test_drivers(self):
        for drivers in ([], ["uart"], ["spi"], ["spi", "uart"]):
            self.manifest["drivers"] = drivers
            self.assertEqual(self.load()[0]["drivers"], drivers)
        for drivers in (None, "uart", ["UART"], ["i2c"], ["spi", "spi"], [False], [{}]):
            self.manifest["drivers"] = drivers
            with self.assertRaises(ValueError):
                self.load()

    def test_defines(self):
        for defines in (
            [],
            None,
            {"bad-name": 1},
            {"A=1": 2},
            {"X": True},
            {"X": 1.0},
            {"X": "1"},
            {"X": None},
        ):
            self.manifest["defines"] = defines
            with self.assertRaises(ValueError):
                self.load()

    def test_schema_and_duplicate_keys(self):
        for text in ("[]", "{}", '{"name":"a","name":"b"}'):
            self.path.write_text(text)
            with self.assertRaises(ValueError):
                builder.load_manifest(self.path)
        self.manifest["unknown"] = 1
        with self.assertRaises(ValueError):
            self.load()

    def test_archive_filter(self):
        root = Path("/archives")
        for drivers in ([], ["uart"], ["spi"], ["uart", "spi"]):
            for driver, component in builder.DRIVER_COMPONENTS.items():
                self.assertEqual(
                    builder.archive_allowed(
                        root / component / ("lib" + component + ".a"), root, drivers
                    ),
                    driver in drivers,
                )
                self.assertEqual(
                    builder.archive_allowed(
                        root / "other" / ("lib" + component + ".a"), root, drivers
                    ),
                    driver in drivers,
                )
            self.assertTrue(builder.archive_allowed(root / "gpio/libgpio.a", root, drivers))
            for component in builder.EXCLUDED_COMPONENTS:
                self.assertFalse(builder.archive_allowed(root / component / "lib.a", root, drivers))

    def test_binding(self):
        good = "3fc80000 00000020 D module_base_binding\n"
        builder.validate_binding(good, 0x3FC80000, bytes(32))
        builder.validate_binding(good.replace("00000020", "00000040"), 0x3FC80000, bytes(64))
        for symbols, data in (
            ("", bytes(32)),
            (good.replace("3fc80000", "3fc80004"), bytes(36)),
            (good.replace("00000020", "0000001f"), bytes(32)),
            (good.replace(" D ", " A "), bytes(32)),
            (good, bytes(31)),
        ):
            with self.assertRaises(ValueError):
                builder.validate_binding(symbols, 0x3FC80000, data)

    def test_symbol_proof_shadow_and_weak(self):
        base = {"socket_exact": (0x42000100, "T"), "weak_api": (0x40001000, "W")}
        symbols = {"socket_exact": (0x42000100, "A"), "weak_api": (0x40001000, "W")}
        relocs = (
            "42100040 00000113 R_RISCV_CALL_PLT 42100080 socket_exact + 0\n"
            "42100044 00000213 R_RISCV_CALL_PLT 40001000 weak_api + 0\n"
        )
        imports, _ = builder.symbol_proof(symbols, base, relocs)
        self.assertNotIn("socket_exact", imports)
        self.assertIn("weak_api", imports)

    def test_symbol_proof(self):
        symbols = {
            "used": (0x4000, "A"),
            "unused": (0x4004, "A"),
            "module_entry": (0x42000028, "T"),
            "module_data": (0x3FC80020, "D"),
        }
        relocs = "42000028 00000113 R_RISCV_CALL_PLT 00004000 used + 0\n"
        imports, owned = builder.symbol_proof(
            symbols, {k: v for k, v in symbols.items() if k in ("used", "unused")}, relocs
        )
        self.assertEqual(set(imports), {"used"})
        self.assertEqual(set(owned), {"module_entry", "module_data"})
        self.assertEqual(imports["used"]["kind"], "A")

    def test_empty_driver_build_and_audits(self):
        """Exercise main end to end with a deterministic offline tool shim."""
        self.load()
        base = self.root / "base.elf"
        base.write_bytes(b"exact resident ELF fixture")
        sdk = self.root / "sdk"
        (sdk / "tools").mkdir(parents=True)
        (sdk / "tools/idf.py").write_text("")
        for component in ("esp_driver_uart", "esp_driver_spi", "gpio", "newlib"):
            archive = self.root / "esp-idf" / component / ("lib" + component + ".a")
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"")
        db = self.root / "compile_commands.json"
        db.write_text(
            json.dumps(
                [
                    dict(
                        file="service.c",
                        directory=str(self.root),
                        arguments=["gcc", "-c", "service.c", "-o", "old.o"],
                    )
                ]
            )
        )
        out = self.root / "result.bin"
        commands = []
        spi_apis = (
            "spi_bus_initialize",
            "spi_bus_add_device",
            "spi_device_queue_trans",
            "spi_device_get_trans_result",
            "spi_bus_remove_device",
            "spi_bus_free",
        )
        mode: dict[str, Any] = {"failure": None, "spi": False}

        def tool(cmd, text=True, cwd=None):
            commands.append(cmd)
            kind = cmd[0].removeprefix("test-")
            if kind == "gcc" and "-dM" in cmd:
                return "#define RMB_SERVICE_ABI 4u\n#define MODULE_ABI_VERSION RMB_SERVICE_ABI\n#define MODULE_DRAM_SIZE 32768\n"
            if kind == "gcc" and "-Wl,--start-group" in cmd:
                (out.parent / "module-link/business.map").write_text("")
                return ""
            if kind == "nm":
                if "-u" in cmd:
                    return " U missing\n" if mode["failure"] == "undefined" else ""
                if Path(cmd[-1]) == base:
                    return (
                        "3fc80000 00008000 B module_data_reserve\n"
                        if "-S" in cmd
                        else "3fc80000 B module_data_reserve\n40000000 T resident_api\n"
                    )
                if str(cmd[-1]).endswith("stdatomic.c.obj"):
                    return "42000000 T __atomic_fetch_add_4\n"
                if str(cmd[-1]).endswith("libesp_driver_uart.a"):
                    return "42000000 T uart_write_bytes\n"
                if str(cmd[-1]).endswith("libesp_driver_spi.a"):
                    return "".join(f"42000000 T {name}\n" for name in spi_apis)
                if "-S" in cmd:
                    return (
                        ""
                        if mode["failure"] == "binding"
                        else "3fc80000 00000020 D module_base_binding\n"
                    )
                return (
                    "3fc80000 A module_data_reserve\n40000000 A resident_api\n"
                    "42000028 T module_entry\n3fc80000 D module_base_binding\n"
                    + (
                        "".join(f"42000028 T business_{name}\n" for name in spi_apis)
                        if mode["spi"]
                        else ""
                    )
                )
            if kind == "objdump":
                return (
                    " 0 .text 00000004 42000028\n ALLOC\n 1 .data 00000020 3fc80000\n ALLOC\n"
                    + (
                        " 2 .orphan 00000004 3fc80020\n ALLOC\n"
                        if mode["failure"] == "orphan"
                        else ""
                    )
                )
            if kind == "readelf":
                return (
                    "42000028 001 R_RISCV_GPREL_I 40000000 resident_api + 0\n"
                    if mode["failure"] == "gp"
                    else "42000028 001 R_RISCV_CALL_PLT 40000000 resident_api + 0\n"
                )
            if kind == "objcopy" and "-O" in cmd:
                Path(cmd[-1]).write_bytes(bytes(4 if "--only-section=.text" in cmd else 32))
            return ""

        argv = [
            "builder",
            "--base-elf",
            str(base),
            "--text-address",
            "0x42000028",
            "--output",
            str(out),
            "--module-manifest",
            str(self.path),
            "--tool-prefix",
            "test-",
        ]
        with (
            patch.dict(os.environ, {"IDF_PATH": str(sdk)}),
            patch("sys.argv", argv),
            patch.object(builder.shutil, "which", return_value="/mock/tool"),
            patch.object(builder.subprocess, "check_output", side_effect=tool),
            patch("builtins.print"),
        ):
            builder.main()
            report = json.loads((self.root / "module-link/proof.json").read_text())
            self.assertEqual(report["name"], "hello-1")
            self.assertEqual(report["drivers"], [])
            self.assertEqual(report["defines"], {"SERVICE_VARIANT": 1})
            self.assertEqual(set(report["imported_base_symbols"]), {"resident_api"})
            self.assertEqual(report["spi_link_ownership"], {})
            self.assertEqual(out.read_bytes()[44:76], bytes.fromhex(report["base_sha256"]))
            self.assertFalse(
                any(c[0] == "test-ar" or any("symbols.txt" in x for x in c) for c in commands)
            )
            link = next(c for c in commands if "-Wl,--start-group" in c)
            self.assertFalse(any("libesp_driver_" in x or "libnewlib" in x for x in link))
            self.assertTrue(any("-DSERVICE_VARIANT=1" in c for c in commands))
            for failure in ("undefined", "binding", "gp", "orphan"):
                mode["failure"] = failure
                with self.subTest(failure=failure), self.assertRaises((ValueError, RuntimeError)):
                    builder.main()
            mode["failure"] = None
            for selected in (["uart"], ["spi"], ["uart", "spi"], None):
                commands.clear()
                mode["spi"] = selected is not None and "spi" in selected
                if selected is None:
                    del argv[argv.index("--module-manifest") : argv.index("--module-manifest") + 2]
                else:
                    self.manifest["drivers"] = selected
                    self.load()
                builder.main()
                report = json.loads((self.root / "module-link/proof.json").read_text())
                expected = selected if selected is not None else ["uart"]
                self.assertEqual(report["drivers"], expected)
                self.assertEqual(any(c[0] == "test-ar" for c in commands), "spi" in expected)
                for driver in ("uart", "spi"):
                    self.assertEqual(
                        any(any(driver + "-symbols.txt" in x for x in c) for c in commands),
                        driver in expected,
                    )
                if selected is None:
                    self.assertIsNone(report["manifest"])
                    self.assertEqual(report["name"], "stm-uart")
                    self.assertEqual(len(report["sources"]), 5)
                    self.assertFalse(
                        any("spi_diagnostic" in source for source in report["sources"])
                    )
                    self.assertEqual(report["defines"], {"RMB_BUILTIN_SPI": 0})
                    self.assertFalse(
                        any("esp_driver_spi" in path for path in report["link_archives"])
                    )
            # Explicit opt-in must keep the same business implementation and SPI ownership audit.
            argv += ["--builtin-service", "stm-spi"]
            mode["spi"] = True
            commands.clear()
            builder.main()
            report = json.loads((self.root / "module-link/proof.json").read_text())
            self.assertEqual(report["name"], "stm-spi")
            self.assertEqual(report["drivers"], ["uart", "spi"])
            self.assertEqual(report["defines"], {"RMB_BUILTIN_SPI": 1})
            self.assertEqual(len(report["sources"]), 6)
            self.assertTrue(any("spi_diagnostic" in source for source in report["sources"]))
            self.assertTrue(any(c[0] == "test-ar" for c in commands))

    def test_builtin_manifest_mutually_exclusive(self):
        argv = [
            "builder",
            "--base-elf",
            "unused.elf",
            "--text-address",
            "0x42000028",
            "--output",
            str(self.root / "must-not-exist.bin"),
            "--module-manifest",
            str(self.path),
            "--builtin-service",
            "stm-spi",
        ]
        with patch("sys.argv", argv), patch.object(builder.subprocess, "check_output") as run:
            with self.assertRaises(SystemExit) as rejected:
                builder.main()
            self.assertEqual(rejected.exception.code, 2)
            run.assert_not_called()
        self.assertFalse((self.root / "must-not-exist.bin").exists())


if __name__ == "__main__":
    unittest.main()
