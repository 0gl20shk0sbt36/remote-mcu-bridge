#!/usr/bin/env python3
"""Link ABI-4 service code against the exact, final resident ESP-IDF ELF.

Resident symbols are GNU ld --just-symbols imports, never copied sections. Only
missing functions are extracted from IDF component archives; the resident owns
FreeRTOS, libc/newlib, heap, startup and the global pointer. Rebuild this image
whenever the resident ELF changes (including changes to its reserved DRAM).
"""

import argparse
import hashlib
import json
import os
import shutil
import pathlib
import re
import shlex
import struct
import subprocess

MAGIC = 0x314D5242
ABI = 4
DRAM_SIZE = 32768
HEADER = struct.Struct("<10I")
# Never extract another copy of any of these process-wide runtimes.
EXCLUDED_COMPONENTS = {
    "main",
    "freertos",
    "newlib",
    "heap",
    "pthread",
    "cxx",
    "riscv",
    "esp_system",
    "bootloader_support",
    "bootloader",
    "unity",
    "cmock",
}


def validate_image(image):
    if len(image) < HEADER.size:
        raise ValueError("short header")
    h = HEADER.unpack_from(image)
    magic, abi, hs, total, ta, ts, da, ds, bs, entry = h
    if (
        magic != MAGIC
        or abi != ABI
        or hs != 40
        or total != len(image)
        or total > 0x100000 - 4096
        or not 0x42000000 <= ta < 0x42800000
        or ta % 65536 != 40
        or ts > 0x42800000 - ta
        or not ts
        or ts % 4
        or ts > 0x100000 - 4096 - 40
        or ds > DRAM_SIZE
        or bs > DRAM_SIZE - ds
        or ts + ds != total - 40
        or not ta <= entry < ta + ts
        or entry % 2
    ):
        raise ValueError("invalid module layout")
    return h


SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
SAFE_MACRO = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
DRIVER_COMPONENTS = {"uart": "esp_driver_uart", "spi": "esp_driver_spi"}


def load_manifest(path):
    """Validate author input before any compile/link side effects."""
    path = pathlib.Path(path).resolve(strict=True)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate manifest key: " + key)
            result[key] = value
        return result

    manifest = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if set(manifest) != {"name", "sources", "drivers", "defines"}:
        raise ValueError("manifest requires exactly name, sources, drivers, defines")
    name = manifest["name"]
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
        raise ValueError("manifest name must be a safe nonempty identifier")
    raw_sources = manifest["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("manifest sources must be a nonempty list")
    sources = []
    for source in raw_sources:
        if not isinstance(source, str) or not source or pathlib.Path(source).is_absolute():
            raise ValueError("manifest sources must be relative C file paths")
        resolved = (path.parent / source).resolve(strict=True)
        if (
            not resolved.is_relative_to(path.parent)
            or not resolved.is_file()
            or resolved.suffix != ".c"
        ):
            raise ValueError("manifest source must be a real C file within manifest directory")
        if resolved in sources:
            raise ValueError("duplicate manifest source: " + source)
        sources.append(resolved)
    drivers = manifest["drivers"]
    if (
        not isinstance(drivers, list)
        or any(not isinstance(d, str) or d not in DRIVER_COMPONENTS for d in drivers)
        or len(set(drivers)) != len(drivers)
    ):
        raise ValueError("manifest drivers must be unique entries from uart, spi")
    defines = manifest["defines"]
    if not isinstance(defines, dict):
        raise ValueError("manifest defines must be an object")
    for key, value in defines.items():
        if not SAFE_MACRO.fullmatch(key) or type(value) is not int:
            raise ValueError("manifest defines require safe macro names and integer values")
    return manifest, sources


def archive_allowed(path, archive_root, drivers):
    component = path.relative_to(archive_root).parts[0]
    return component not in EXCLUDED_COMPONENTS and all(
        component != c and path.name != "lib" + c + ".a"
        for d, c in DRIVER_COMPONENTS.items()
        if d not in drivers
    )


def validate_binding(symbols_with_sizes, data_address, data):
    match = re.search(
        r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([A-Za-z])\s+module_base_binding$",
        symbols_with_sizes,
        re.M,
    )
    if (
        not match
        or int(match[1], 16) != data_address
        or int(match[2], 16) < 32
        or match[3].upper() not in ("D", "R", "S", "G")
        or len(data) < 32
    ):
        raise ValueError(
            "module_base_binding must occupy at least the first 32 initialized bytes at data address"
        )


def symbol_proof(symbols, base, relocs):
    referenced = set()
    for line in relocs.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[2].startswith("R_RISCV_"):
            # Local symbols may shadow resident globals with the same name.
            # Relocation target ADDRESS, not just spelling, proves an import.
            if re.fullmatch(r"[0-9a-fA-F]+", fields[3]):
                referenced.add((fields[4], int(fields[3], 16)))

    def describe(items):
        return {
            name: {"address": hex(address), "kind": kind} for name, (address, kind) in sorted(items)
        }

    imported = describe(
        (name, value)
        for name, value in symbols.items()
        if (name, value[0]) in referenced
        and name in base
        and value[1] in ("A", "W", "V")
        and value[0] == base[name][0]
    )
    defined = describe(
        (name, value)
        for name, value in symbols.items()
        if name not in base or value[0] != base[name][0]
    )
    return imported, defined


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-elf", required=True)
    service = ap.add_mutually_exclusive_group()
    service.add_argument(
        "--builtin-service",
        choices=("stm-uart", "stm-spi"),
        default="stm-uart",
        help="builtin service (default: stm-uart, no GP-SPI)",
    )
    service.add_argument(
        "--module-manifest", help="JSON service name, sources, drivers and integer defines"
    )
    ap.add_argument("--compile-commands", help="defaults to compile_commands.json beside base ELF")
    ap.add_argument(
        "--text-address",
        required=True,
        type=lambda x: int(x, 0),
        help="actual module_runtime_text_address from target status; never guessed",
    )
    ap.add_argument(
        "--data-address",
        type=lambda x: int(x, 0),
        help="optional target-reported address; checked against resident ELF",
    )
    ap.add_argument("--output", required=True)
    ap.add_argument(
        "--tool-prefix",
        default=os.environ.get(
            "RISCV_TOOL_PREFIX", os.environ.get("CROSS_COMPILE", "riscv32-esp-elf-")
        ),
        help="tool path/name prefix (default: RISCV_TOOL_PREFIX, CROSS_COMPILE, or riscv32-esp-elf- on PATH)",
    )
    a = ap.parse_args()
    manifest_path = pathlib.Path(a.module_manifest).resolve() if a.module_manifest else None
    manifest, manifest_sources = load_manifest(manifest_path) if manifest_path else (None, None)
    drivers = (
        manifest["drivers"]
        if manifest is not None
        else ["uart", "spi"]
        if a.builtin_service == "stm-spi"
        else ["uart"]
    )
    defines = (
        manifest["defines"]
        if manifest is not None
        else {"RMB_BUILTIN_SPI": int(a.builtin_service == "stm-spi")}
    )
    idf_path = os.environ.get("IDF_PATH")
    if not idf_path or not (pathlib.Path(idf_path) / "tools/idf.py").is_file():
        ap.error("IDF_PATH must point to an installed ESP-IDF checkout (tools/idf.py required)")
    missing = [
        a.tool_prefix + tool
        for tool in ("gcc", "nm", "objcopy", "objdump", "readelf", "ar")
        if shutil.which(a.tool_prefix + tool) is None
    ]
    if missing:
        ap.error(
            "missing tool(s): "
            + ", ".join(missing)
            + "; set PATH, RISCV_TOOL_PREFIX, or --tool-prefix"
        )
    here = pathlib.Path(__file__).resolve().parent
    root = here.parent.parent
    base_elf = pathlib.Path(a.base_elf).resolve()
    build = base_elf.parent
    database = (
        pathlib.Path(a.compile_commands).resolve()
        if a.compile_commands
        else build / "compile_commands.json"
    )
    out = pathlib.Path(a.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / "module-link"
    work.mkdir(exist_ok=True)
    commands = []

    def run(tool, *args, cwd=None):
        cmd = [a.tool_prefix + tool, *map(str, args)]
        commands.append(cmd)
        return subprocess.check_output(cmd, text=True, cwd=cwd)

    def syms(elf):
        result = {}
        for line in run("nm", "-g", "--defined-only", elf).splitlines():
            fields = line.split()
            if len(fields) == 3:
                result[fields[2]] = (int(fields[0], 16), fields[1])
        return result

    base_hash = hashlib.sha256(base_elf.read_bytes()).hexdigest()
    base = syms(base_elf)
    ta = a.text_address
    if "module_data_reserve" not in base:
        raise ValueError("base ELF lacks module_data_reserve; build the compile-enabled profile")
    da = base["module_data_reserve"][0]
    reserve = re.search(
        r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+\w\s+module_data_reserve$",
        run("nm", "-S", "--defined-only", base_elf),
        re.M,
    )
    if not reserve or int(reserve[2], 16) != DRAM_SIZE:
        raise ValueError(f"base ELF must reserve module_data_reserve[{DRAM_SIZE}] for ABI-{ABI}")
    if not 0x42000000 <= ta < 0x42800000 or ta % 65536 != HEADER.size:
        raise ValueError("text address must be target IROM mapping base + 40-byte header")
    if a.data_address is not None and a.data_address != da:
        raise ValueError("target data address differs from resident ELF")

    records = json.loads(database.read_text())
    template = next((r for r in records if pathlib.Path(r["file"]).name == "service.c"), None)
    if template is None:
        raise ValueError("compile_commands.json lacks resident service.c compile configuration")
    original = template.get("arguments") or shlex.split(template["command"])
    # Reuse the real generated sdkconfig, component include paths, defines,
    # toolchain response file and target flags. Replace only input/output.
    flags = []
    i = 1
    while i < len(original):
        arg = original[i]
        if arg in ("-o", "-MF", "-MT", "-MQ"):
            i += 2
            continue
        if arg in ("-c", "-MD", "-MMD", "-MP") or arg == template["file"]:
            i += 1
            continue
        flags.append(arg)
        i += 1
    flags += [
        "-I" + str(root / "include"),
        "-I" + str(root / "rom"),
        "-I" + str(here.parent / "main"),
        "-I" + str(here),
        "-Os",
        "-msmall-data-limit=0",
        "-mno-relax",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-common",
        "-ffunction-sections",
        "-fdata-sections",
    ]
    flags += [f"-D{name}={value}" for name, value in sorted(defines.items())]
    macros = run(
        "gcc",
        *flags,
        "-dM",
        "-E",
        "-include",
        "module_abi.h",
        "-x",
        "c",
        "/dev/null",
        cwd=template["directory"],
    )
    # -dM preserves aliases; check the SDK literal and runtime capacity.
    for name, expected in (("RMB_SERVICE_ABI", ABI), ("MODULE_DRAM_SIZE", DRAM_SIZE)):
        value = re.search(r"^#define " + name + r"\s+(\d+)[uUlL]*\s*$", macros, re.M)
        if not value or int(value[1]) != expected:
            raise ValueError(f"{name} must be {expected} in the actual IDF compile configuration")
    sources = [
        root / "rom/rom.c",
        root / "rom/devices.c",
        here / "entry.c",
        here / "business.c",
        here / "platform/esp_idf_io.c",
    ]
    if manifest_sources is None and a.builtin_service == "stm-spi":
        sources.append(here / "logic/spi_diagnostic.c")
    if manifest_sources is not None:
        sources = manifest_sources
    objs = []
    for n, source in enumerate(sources):
        if not source.is_file():
            raise ValueError("missing required module source: " + str(source))
        obj = work / f"{n}-{source.stem}.o"
        run("gcc", *flags, "-c", source, "-o", obj, cwd=template["directory"])
        objs.append(obj)

    ld = work / "module.ld"
    ld.write_text(f"""ENTRY(module_entry)
MEMORY {{ CODE (rx) : ORIGIN = 0x{ta:x}, LENGTH = {0x100000 - 4096 - 40}
 DATA (rw) : ORIGIN = 0x{da:x}, LENGTH = {DRAM_SIZE} }}
SECTIONS {{
 .text : {{ *(.literal .literal.* .text .text.* .iram .iram.* .iram0.literal .iram0.text .iram1 .iram1.*) . = ALIGN(4); }} > CODE
 .data : {{ KEEP(*(.module_base_id)) *(.rodata .rodata.* .srodata .srodata.* .data .data.* .sdata .sdata.* .dram .dram.* .dram0.data .dram1 .dram1.* .sdata2 .sdata2.* .got .got.*) . = ALIGN(4); }} > DATA
 .bss (NOLOAD) : {{ *(.bss .bss.* .sbss .sbss.* .sbss2 .sbss2.* .noinit .noinit.* COMMON) . = ALIGN(4); }} > DATA
 /DISCARD/ : {{ *(.comment .eh_frame .eh_frame.* .riscv.attributes .note.GNU-stack) }}
}}
""")
    archives = sorted(
        p
        for p in (build / "esp-idf").rglob("*.a")
        if archive_allowed(p, build / "esp-idf", drivers)
    )
    if not archives:
        raise ValueError("no built IDF static archives beside base ELF")
    # Console UART0 VFS can retain IDF uart APIs in the base. Never mix those
    # APIs' private p_uart_obj with the independently linked UART1 driver's
    # state. Namespace ALL public uart.c APIs and every module reference.
    if "uart" in drivers:
        uart_archive = build / "esp-idf/esp_driver_uart/libesp_driver_uart.a"
        uart_names = sorted(
            {
                line.split()[-1]
                for line in run("nm", "-g", "--defined-only", uart_archive).splitlines()
                if len(line.split()) == 3 and line.split()[-1].startswith("uart_")
            }
        )
        rename = work / "uart-symbols.txt"
        rename.write_text("".join(name + " business_" + name + "\n" for name in uart_names))
        for obj in objs:
            run("objcopy", "--redefine-syms=" + str(rename), obj)
        namespaced_uart = work / "libbusiness_uart.a"
        run("objcopy", "--redefine-syms=" + str(rename), uart_archive, namespaced_uart)
        archives = [namespaced_uart if p == uart_archive else p for p in archives]
    # Own the entire GP-SPI driver's state, even if future bases retain an API.
    # SPI flash uses its separate resident spi_flash driver, never these APIs.
    if "spi" in drivers:
        spi_archive = build / "esp-idf/esp_driver_spi/libesp_driver_spi.a"
        spi_names = sorted(
            {
                line.split()[-1]
                for line in run("nm", "-g", "--defined-only", spi_archive).splitlines()
                if len(line.split()) == 3
            }
        )
        spi_rename = work / "spi-symbols.txt"
        spi_rename.write_text("".join(name + " business_" + name + "\n" for name in spi_names))
        for obj in objs:
            run("objcopy", "--redefine-syms=" + str(spi_rename), obj)
        namespaced_spi = work / "libbusiness_spi.a"
        run("objcopy", "--redefine-syms=" + str(spi_rename), spi_archive, namespaced_spi)
        archives = [namespaced_spi if p == spi_archive else p for p in archives]
        # C3 GP-SPI lock needs missing 32-bit compiler atomics. Extract ONLY this
        # stateless IDF object, never the newlib runtime/heap/locks archive as a whole.
        atomic_obj = work / "stdatomic.c.obj"
        run("ar", "x", build / "esp-idf/newlib/libnewlib.a", "stdatomic.c.obj", cwd=work)
        atomic_symbols = syms(atomic_obj)
        if any(kind.upper() in ("B", "D", "S", "G", "C") for _, kind in atomic_symbols.values()):
            raise RuntimeError("atomic helper unexpectedly contains process-wide mutable state")
        atomic_archive = work / "libbusiness_atomics.a"
        run("ar", "rcs", atomic_archive, atomic_obj)
        archives.append(atomic_archive)
    elf = work / "business.elf"
    # FIRST definitions are resident absolutes. Some IDF archives package many
    # API functions into one object: allow overlapping definitions, then GC the
    # unused copies. Audit below requires every resident global to stay absolute.
    link_flags = [
        "-nostdlib",
        "-nostartfiles",
        "-march=rv32imc_zicsr_zifencei",
        "-mabi=ilp32",
        "-mno-relax",
        "-Wl,--no-relax",
        "-Wl,--gc-sections",
        "-Wl,--allow-multiple-definition",
        "-Wl,--emit-relocs",
        "-Wl,--just-symbols=" + str(base_elf),
        "-Wl,-Map=" + str(work / "business.map"),
        "-T",
        str(ld),
    ]
    run("gcc", *link_flags, "-o", elf, *objs, "-Wl,--start-group", *archives, "-Wl,--end-group")
    undef = run("nm", "-u", elf).strip()
    if undef:
        raise RuntimeError("undefined imports: " + undef)
    symbols = syms(elf)
    spi_ownership = {
        name: {"address": hex(address), "kind": kind}
        for name, (address, kind) in symbols.items()
        if name.startswith(("business_spi_", "business_spicommon_", "spi_hal_", "spi_bus_lock_"))
    }
    if "spi" in drivers:
        for name in (
            "spi_bus_initialize",
            "spi_bus_add_device",
            "spi_device_queue_trans",
            "spi_device_get_trans_result",
            "spi_bus_remove_device",
            "spi_bus_free",
        ):
            address, kind = symbols["business_" + name]
            if kind.upper() != "T" or not ta <= address < ta + 0x100000 - 4096 - 40:
                raise RuntimeError("SPI API is not module-owned: " + name)
    # GNU ld retains W/V kinds for resident weak absolutes under --just-symbols.
    # Exact resident addresses, not kind A alone, establish non-copy ownership.
    duplicates = [name for name in symbols if name in base and symbols[name][0] != base[name][0]]
    if duplicates:
        raise RuntimeError(
            "resident runtime/global definitions copied into module: " + ", ".join(duplicates)
        )
    sections = run("objdump", "-h", elf)
    (work / "sections.txt").write_text(sections)
    (work / "relocations.txt").write_text(run("readelf", "-rW", elf))
    # Allocatable orphan sections would not be serialized by the image format.
    rows = sections.splitlines()
    for n, row in enumerate(rows[:-1]):
        m = re.match(r"\s*\d+\s+(\S+)\s+([0-9a-fA-F]+)\s+", row)
        if (
            m
            and int(m[2], 16)
            and "ALLOC" in rows[n + 1]
            and m[1] not in (".text", ".data", ".bss")
        ):
            raise RuntimeError("unserialized allocatable section: " + m[1])
    relocs = (work / "relocations.txt").read_text()
    # Archive objects may retain R_RISCV_RELAX *annotations*. They are not
    # applied because the final link uses --no-relax; GP-relative code is not OK.
    if re.search(r"R_RISCV_GPREL", relocs):
        raise RuntimeError("module contains forbidden GP-relative relocations")

    def section(name):
        path = work / (name + ".bin")
        run("objcopy", "-O", "binary", "--only-section=." + name, elf, path)
        return path.read_bytes()

    text = section("text")
    raw_data = section("data")
    validate_binding(run("nm", "-S", "-g", "--defined-only", elf), da, raw_data)
    data = bytes.fromhex(base_hash) + section("data")[32:]
    match = re.search(r"\s\.bss\s+([0-9a-fA-F]+)\s", sections)
    bss = int(match[1], 16) if match else 0
    entry = symbols["module_entry"][0]
    header = HEADER.pack(
        MAGIC,
        ABI,
        HEADER.size,
        HEADER.size + len(text) + len(data),
        ta,
        len(text),
        da,
        len(data),
        bss,
        entry,
    )
    image = header + text + data
    validate_image(image)
    if hashlib.sha256(base_elf.read_bytes()).hexdigest() != base_hash:
        raise RuntimeError("base ELF changed during module build; rerun against final ELF")
    out.write_bytes(image)
    link_map = (work / "business.map").read_text()
    extracted = sorted(set(re.findall(r"([^\s]+\.a)\(([^)]+)\)", link_map)))
    imported_base_symbols, module_defined_globals = symbol_proof(symbols, base, relocs)
    report = {
        "manifest": str(manifest_path) if manifest_path else None,
        "name": manifest["name"] if manifest is not None else a.builtin_service,
        "builtin_service": None if manifest is not None else a.builtin_service,
        "drivers": drivers,
        "defines": defines,
        "imported_base_symbols": imported_base_symbols,
        "module_defined_globals": module_defined_globals,
        "artifact": str(out),
        "base_elf": str(base_elf),
        "base_sha256": base_hash,
        "compile_commands": str(database),
        "text_address": hex(ta),
        "text_bytes": len(text),
        "data_address": hex(da),
        "data_bytes": len(data),
        "bss_bytes": bss,
        "dram_capacity": DRAM_SIZE,
        "entry": hex(entry),
        "abi": ABI,
        "undefined_symbols": undef,
        "duplicate_resident_definitions": duplicates,
        "gp_relaxation": False,
        "header_bytes": HEADER.size,
        "spi_link_ownership": spi_ownership,
        "sources": list(map(str, sources)),
        "link_flags": link_flags,
        "link_archives": list(map(str, archives)),
        "excluded_components": sorted(EXCLUDED_COMPONENTS),
        "extracted_archive_members": [list(x) for x in extracted],
    }
    (work / "commands.json").write_text(json.dumps(commands, indent=2))
    (work / "proof.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
