#!/usr/bin/env python3
"""Real hardware-free module link check. Every output is NOT DEPLOYABLE."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-elf", type=Path, required=True)
    parser.add_argument("--builtin-service", choices=("stm-uart", "stm-spi"), default="stm-uart")
    parser.add_argument("--compile-commands", type=Path)
    parser.add_argument("--output-dir", type=Path, help="dedicated compilecheck directory")
    parser.add_argument(
        "--tool-prefix",
        default=os.environ.get(
            "RISCV_TOOL_PREFIX", os.environ.get("CROSS_COMPILE", "riscv32-esp-elf-")
        ),
    )
    args = parser.parse_args()
    idf = os.environ.get("IDF_PATH")
    if not idf or not (Path(idf) / "tools/idf.py").is_file():
        parser.error("IDF_PATH must point to an installed ESP-IDF checkout")
    if not shutil.which(args.tool_prefix + "nm"):
        parser.error("nm not found; set PATH, RISCV_TOOL_PREFIX, or --tool-prefix")
    base = args.base_elf.resolve()
    if not base.is_file():
        parser.error("base ELF does not exist")
    # Use actual resident symbol bounds, not an invented resident or target status.
    raw = subprocess.check_output(
        [args.tool_prefix + "nm", "-S", "--defined-only", str(base)], text=True
    )
    highest = 0x42000000
    data_address = None
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) not in (3, 4):
            continue
        address = int(fields[0], 16)
        size = int(fields[1], 16) if len(fields) == 4 else 0
        if 0x42000000 <= address < 0x42800000:
            highest = max(highest, address + size)
        if fields[-1] == "module_data_reserve":
            if size != 32768:
                parser.error("enabled resident must reserve 32768 bytes for module data")
            data_address = address
    if data_address is None:
        parser.error("base lacks module_data_reserve; use the compile-enabled resident ELF")
    text_address = ((highest + 65535) & ~65535) + 40
    if text_address + 0x100000 - 4096 - 40 > 0x42800000:
        parser.error("insufficient synthetic IROM address space for compilecheck")
    out = (args.output_dir or base.parent / "module-compilecheck").resolve()
    out.mkdir(parents=True, exist_ok=True)
    warning = "NOT DEPLOYABLE: synthetic text VA; no hardware mapping or runtime validation."
    (out / "NOT-DEPLOYABLE.txt").write_text(warning + "\n")
    image = out / "business.NOT-DEPLOYABLE.bin"
    command = [
        sys.executable,
        str(ROOT / "esp32_rom/module/build_module.py"),
        "--base-elf",
        str(base),
        "--text-address",
        hex(text_address),
        "--data-address",
        hex(data_address),
        "--tool-prefix",
        args.tool_prefix,
        "--output",
        str(image),
    ]
    if args.builtin_service != "stm-uart":
        command += ["--builtin-service", args.builtin_service]
    if args.compile_commands:
        command += ["--compile-commands", str(args.compile_commands.resolve())]
    print(warning, flush=True)
    subprocess.run(command, check=True)
    spec = importlib.util.spec_from_file_location(
        "module_builder", ROOT / "esp32_rom/module/build_module.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    header = builder.validate_image(image.read_bytes())
    proof = json.loads((out / "module-link/proof.json").read_text())
    if (
        header[4] != text_address
        or header[6] != data_address
        or proof["base_sha256"] != hashlib.sha256(base.read_bytes()).hexdigest()
        or proof["undefined_symbols"]
        or proof["duplicate_resident_definitions"]
    ):
        raise RuntimeError("module link proof/header verification failed")
    if args.builtin_service == "stm-uart":
        if (
            proof["drivers"] != ["uart"]
            or proof["defines"].get("RMB_BUILTIN_SPI") != 0
            or any("spi_diagnostic" in s for s in proof["sources"])
            or any(
                "esp_driver_spi" in path or "libbusiness_spi" in path
                for path in proof["link_archives"]
            )
            or any(
                "esp_driver_spi" in member[0] or "libbusiness_spi" in member[0]
                for member in proof["extracted_archive_members"]
            )
            or any(
                name.startswith(
                    (
                        "esp_io_spi_",
                        "spi_diagnostic_",
                        "business_spi_",
                        "business_spicommon_",
                        "spi_patterns",
                        "spi_parse_line",
                        "spi_parameters_valid",
                    )
                )
                for name in proof["module_defined_globals"]
            )
        ):
            raise RuntimeError(
                "default builtin unexpectedly includes GP-SPI sources/driver/symbols"
            )
        if any(
            marker in image.read_bytes()
            for marker in (b"spi-test", b"spi-pad-sample", b"SPI ARM ", b"pad_scope", b"spi_host")
        ):
            raise RuntimeError("default builtin contains SPI handler/status strings")
    result = {
        "deployable": False,
        "builtin_service": args.builtin_service,
        "warning": warning,
        "base_elf": str(base),
        "base_sha256": proof["base_sha256"],
        "synthetic_text_address": hex(text_address),
        "resident_data_address": hex(data_address),
        "image": str(image),
        "header_validated": True,
        "real_link_completed": True,
    }
    (out / "compilecheck.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
