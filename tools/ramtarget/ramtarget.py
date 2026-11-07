#!/usr/bin/env python3
"""Add an SRAM-execution build target to a CubeMX / GNU-ld ARM project.

What it does
------------
Given a project whose linker script places code in Flash, this tool derives a
second linker script that places everything (vector table first) at the start of
executable SRAM, and generates a CMake include that switches between the two
layouts. The resulting image can be uploaded into SRAM by a bootloader -- e.g.
the STM32 factory ROM UART loader -- and jumped to without touching Flash.

Commands
--------
    ramtarget.py devices
    ramtarget.py gen   PROJECT --device STM32F407ZGT6 [--reserve 0x4000]
    ramtarget.py verify --bin IMAGE --target ram|flash --device STM32F407ZGT6

Design notes
------------
* Device facts live in devices.json (memory map, ROM-loader footprint, vector
  alignment, how the vendor startup code relocates VTOR). Adding a new part is
  a data change, not a code change -- but only add a part after checking its
  reference manual.
* The linker transform is textual: the source script's MEMORY block decides
  which region is Flash and which is executable SRAM, so the same code works for
  scripts with different region names (FLASH/ROM/FLASH_BANK1, RAM/SRAM/...).
* `verify` implements the checks a loader performs before it jumps: initial MSP,
  Thumb bit, reset vector inside the uploaded image, base alignment.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEVICES = HERE / "devices.json"


# --------------------------------------------------------------------------- #
# sizes and memory regions
# --------------------------------------------------------------------------- #
def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*(0[xX][0-9a-fA-F]+|\d+)\s*([kKmM]?)\s*", text)
    if not m:
        raise ValueError(f"cannot parse size: {text!r}")
    value = int(m.group(1), 0)
    suffix = m.group(2).lower()
    return value * (1024 if suffix == "k" else 1024 * 1024 if suffix == "m" else 1)


def fmt_size(value: int) -> str:
    if value % 1024 == 0 and (value // 1024) % 4 == 0:
        return f"{value // 1024}K"
    return f"0x{value:x}"


MEMORY_RE = re.compile(r"MEMORY\s*\{(?P<body>[^}]*)\}", re.S)
REGION_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<attrs>[^)]*)\)\s*:\s*"
    r"ORIGIN\s*=\s*(?P<origin>0[xX][0-9a-fA-F]+|\d+)\s*,\s*"
    r"LENGTH\s*=\s*(?P<length>[^,\n]+)"
)


def parse_memory(script: str) -> dict[str, dict]:
    match = MEMORY_RE.search(script)
    if not match:
        raise ValueError("no MEMORY block found in linker script")
    regions = {}
    for m in REGION_RE.finditer(match.group("body")):
        regions[m.group("name")] = {
            "origin": int(m.group("origin"), 0),
            "length": parse_size(m.group("length")),
            "attrs": m.group("attrs").strip(),
            "raw_length": m.group("length").strip(),
        }
    if not regions:
        raise ValueError("MEMORY block has no parsable regions")
    return regions


# --------------------------------------------------------------------------- #
# device database
# --------------------------------------------------------------------------- #
def load_devices() -> dict[str, dict]:
    data = json.loads(DEVICES.read_text())
    devices = data["devices"]
    resolved = {}
    for name, entry in devices.items():
        resolved[name] = resolve(name, devices, seen=())
    return resolved


def resolve(name: str, devices: dict, seen: tuple) -> dict:
    if name in seen:
        raise ValueError(f"inheritance cycle at {name}")
    if name not in devices:
        raise KeyError(f"unknown device {name}")
    base_name = devices[name].get("extends")
    if not base_name:
        return dict(devices[name])
    merged = resolve(base_name, devices, seen + (name,))
    merged.update({k: v for k, v in devices[name].items() if k != "extends"})
    # keep the inherited reference visible in the merged entry
    merged.setdefault("reference", devices[base_name].get("reference"))
    return merged


def detect_device(project: pathlib.Path) -> str | None:
    for ioc in project.glob("*.ioc"):
        for line in ioc.read_text(errors="replace").splitlines():
            if line.startswith("Mcu.CPN="):
                return line.split("=", 1)[1].strip()
    return None



def patch_vtor_guard(project: pathlib.Path, device: dict) -> str | None:
    """Let the build override VECT_TAB_OFFSET through -D.

    Some vendor startup files hardcode `#define VECT_TAB_OFFSET 0x0` inside the
    relocation block (STM32F1 CubeF1 does; newer F4 files guard it). Without a
    guard a -D on the command line is silently overridden, and the RAM image
    would set VTOR to the SRAM base instead of the image base -- an interrupt
    would then fetch a garbage vector. Wrap the definition in #if !defined.
    """
    rel = (device.get("vtor") or {}).get("guard_file")
    if not rel:
        return None
    path = project / rel
    if not path.exists():
        raise SystemExit(f"error: {rel} not found; cannot make VECT_TAB_OFFSET overridable")
    src = path.read_text()
    if "#if !defined(VECT_TAB_OFFSET)" in src:
        return f"{rel} (already guarded)"
    pattern = re.compile(
        r"(?P<define>#define\s+VECT_TAB_OFFSET\s+0x0+U\s*/\*!<[^*]*?\*/)"
    )
    matches = pattern.findall(src)
    if not matches:
        raise SystemExit(f"error: no unguarded VECT_TAB_OFFSET in {rel}; "
                         "check whether the vendor template changed")
    out = pattern.sub(lambda m: '#if !defined(VECT_TAB_OFFSET)\n' + m.group('define')
                      + '\n#endif /* VECT_TAB_OFFSET */', src)
    path.write_text(out)
    return f"{rel} ({len(matches)} definition(s) guarded)"


# --------------------------------------------------------------------------- #
# linker script transform
# --------------------------------------------------------------------------- #
def pick_flash_region(regions: dict, device: dict) -> str:
    wanted = int(device["flash"]["origin"], 0)
    for name, r in regions.items():
        if r["origin"] == wanted:
            return name
    raise ValueError(f"no MEMORY region at Flash origin {device['flash']['origin']}")


def pick_exec_region(regions: dict, device: dict) -> str:
    """Region named by the device entry, falling back to 'first executable SRAM'."""
    sram = device["sram_exec"][0]
    wanted = int(sram["origin"], 0)
    for name, r in regions.items():
        if r["origin"] == wanted and "x" in r["attrs"]:
            return name
    for name, r in regions.items():
        if r["origin"] == wanted:
            return name
    raise ValueError(f"no MEMORY region at SRAM origin {sram['origin']}")


def build_ram_script(src: str, device: dict, reserve: int | None = None) -> tuple[str, dict]:
    regions = parse_memory(src)
    flash_name = pick_flash_region(regions, device)
    exec_name = pick_exec_region(regions, device)

    sram = device["sram_exec"][0]
    sram_origin = parse_size(sram["origin"])
    sram_len = regions[exec_name]["length"]
    reserve = device["rom_reserve_bytes"] if reserve is None else reserve
    reserve = parse_size(reserve) if isinstance(reserve, str) else int(reserve)
    if reserve >= sram_len:
        raise ValueError("reserve must be smaller than the executable SRAM region")

    base = sram_origin + reserve
    new_len = sram_len - reserve
    align = int(device.get("vector_align", 4))
    if base % align:
        raise ValueError(f"base 0x{base:x} is not {align}-byte aligned; adjust reserve")

    out = src
    # 1) shrink the executable SRAM region to the app window, starting after the
    #    pre-boot stage that must stay intact.
    old_region_re = re.compile(
        rf"(?P<name>(?<![A-Za-z0-9_]){re.escape(exec_name)}\b\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*)"
        r"(?:0[xX][0-9a-fA-F]+|\d+)(?P<mid>\s*,\s*LENGTH\s*=\s*)[^,\n]+"
    )
    out, n = old_region_re.subn(
        lambda m: f"{m.group('name')}0x{base:08x}{m.group('mid')}{fmt_size(new_len)}", out
    )
    if n != 1:
        raise ValueError(f"expected one definition of region {exec_name}, found {n}")

    # 2) every output section that lived in Flash now runs (and loads) from SRAM.
    out = re.sub(rf"AT>\s*{re.escape(flash_name)}\b", f"AT> {exec_name}", out)
    out = re.sub(rf">\s*{flash_name}\b", f"> {exec_name}", out)

    # 3) make the contract explicit in the script itself.
    banner = (
        f"/* Generated by tools/ramtarget: SRAM-execution variant.\n"
        f" * Image base 0x{base:08x}, window {new_len // 1024} KiB, "
        f"0x{reserve:x} bytes reserved at the start of {sram['name']} for the pre-boot stage.\n"
        f" * LMA == VMA everywhere, so the loader only copies the raw image.\n"
        f" */\n"
    )
    assertions = (
        f"\nASSERT(ORIGIN({exec_name}) == 0x{base:08x}, \"RAM image base mismatch\")\n"
        f"ASSERT((ORIGIN({exec_name}) % {align}) == 0, \"vector table alignment\")\n"
        f"_ram_image_base = ORIGIN({exec_name});\n"
        f"_ram_image_limit = ORIGIN({exec_name}) + LENGTH({exec_name});\n"
    )
    meta = {
        "flash_region": flash_name,
        "exec_region": exec_name,
        "base": base,
        "length": new_len,
        "reserve": reserve,
        "align": align,
    }
    return banner + out.rstrip() + "\n" + assertions, meta


def vtor_defines(device: dict, meta: dict) -> list[str]:
    vtor = device.get("vtor", {})
    kind = vtor.get("type")
    if kind == "stm32_vect_tab":
        offset = meta["base"] - int(vtor["sram_base"], 0)
        return ["USER_VECT_TAB_ADDRESS", "VECT_TAB_SRAM", f"VECT_TAB_OFFSET=0x{offset:04X}U"]
    if kind == "none":
        return []
    raise ValueError(f"unsupported vtor type {kind!r}")


# --------------------------------------------------------------------------- #
# CMake include
# --------------------------------------------------------------------------- #
CMAKE_TEMPLATE = """\
# Generated by tools/ramtarget -- do not edit by hand.
# Regenerate with: {regenerate}
#
# Adds APP_TARGET=flash|ram. Flash keeps the vendor linker script; ram swaps in
# {ram_ld_name} (image base 0x{base:08x}, {length} bytes, {reserve} bytes reserved
# for the pre-boot stage) and relocates VTOR through the vendor startup code.

set(APP_TARGET "{default_target}" CACHE STRING "Link target: flash or ram")

if(APP_TARGET STREQUAL "ram")
    target_compile_definitions(${{CMAKE_PROJECT_NAME}} PRIVATE
{defines_main}
    )
{defines_kernel}
    if(NOT CMAKE_EXE_LINKER_FLAGS MATCHES "{flash_ld_name}")
        message(FATAL_ERROR "Expected the toolchain file to reference {flash_ld_name}")
    endif()
    # A second -T only merges MEMORY blocks; ld keeps the first script's SECTIONS,
    # so replace the script path in place instead of appending.
    string(REPLACE "{flash_ld_name}" "{ram_ld_name}"
           CMAKE_EXE_LINKER_FLAGS "${{CMAKE_EXE_LINKER_FLAGS}}")
elseif(APP_TARGET STREQUAL "flash")
    # Nothing to do: vendor linker script, vector table at Flash base.
else()
    message(FATAL_ERROR "APP_TARGET must be flash or ram, got '${{APP_TARGET}}'")
endif()

# Emit the raw image the loader uploads, then check it the way the loader will.
add_custom_command(TARGET ${{CMAKE_PROJECT_NAME}} POST_BUILD
    COMMAND ${{CMAKE_OBJCOPY}} -O binary $<TARGET_FILE:${{CMAKE_PROJECT_NAME}}>
            $<TARGET_FILE_DIR:${{CMAKE_PROJECT_NAME}}>/${{CMAKE_PROJECT_NAME}}.bin
    COMMAND ${{CMAKE_OBJCOPY}} -O ihex $<TARGET_FILE:${{CMAKE_PROJECT_NAME}}>
            $<TARGET_FILE_DIR:${{CMAKE_PROJECT_NAME}}>/${{CMAKE_PROJECT_NAME}}.hex
    COMMAND ${{Python3_EXECUTABLE}} {generator} verify
            --bin $<TARGET_FILE_DIR:${{CMAKE_PROJECT_NAME}}>/${{CMAKE_PROJECT_NAME}}.bin
            --target ${{APP_TARGET}} --device {device}
    COMMENT "objcopy + image verification"
)
"""


def emit_cmake(device_name: str, device: dict, meta: dict, generator: pathlib.Path,
               flash_ld_name: str, ram_ld_name: str, default_target: str = "flash") -> str:
    defines = vtor_defines(device, meta)
    defines_main = "\n".join(f"        {d}" for d in defines) or "        # (none required)"
    kernel_libs = device.get("kernel_object_libs") or []
    if kernel_libs:
        lines = []
        for lib in kernel_libs:
            lines.append(f"    # {lib} compiles vendor startup/clock code out of tree,")
            lines.append(f"    # so it needs the same vector-table macros.")
            lines.append(f"    if(NOT TARGET {lib})")
            lines.append(f'        message(FATAL_ERROR "{lib} target missing: cannot relocate VTOR")')
            lines.append("    endif()")
            lines.append(f"    target_compile_definitions({lib} PRIVATE")
            lines.extend(f"        {d}" for d in defines)
            lines.append("    )")
        defines_kernel = "\n".join(lines) + "\n"
    else:
        defines_kernel = ""
    python_guard = (
        "if(NOT Python3_EXECUTABLE)\n"
        "    find_package(Python3 COMPONENTS Interpreter REQUIRED)\n"
        "endif()\n\n"
    )
    body = CMAKE_TEMPLATE.format(
        regenerate=f"{generator} gen . --device {device_name}",
        ram_ld_name=ram_ld_name,
        flash_ld_name=flash_ld_name,
        base=meta["base"],
        length=meta["length"],
        reserve=meta["reserve"],
        defines_main=defines_main,
        defines_kernel=defines_kernel,
        generator=generator,
        device=device_name,
        default_target=default_target,
    )
    return body.replace("set(APP_TARGET", python_guard + "set(APP_TARGET", 1)


# --------------------------------------------------------------------------- #
# image verification (what a loader must check before jumping)
# --------------------------------------------------------------------------- #
def verify_image(path: pathlib.Path, target: str, device: dict) -> list[str]:
    problems = []
    data = path.read_bytes()
    if target == "ram":
        base = int(device["sram_exec"][0]["origin"], 0) + int(device["rom_reserve_bytes"])
        limit = int(device["sram_exec"][0]["origin"], 0) + parse_size(device["sram_exec"][0]["length"])
    else:
        base = int(device["flash"]["origin"], 0)
        limit = base + parse_size(device["flash"]["length"])
    if len(data) < 8:
        return [f"image too small ({len(data)} bytes)"]
    if base + len(data) > limit:
        problems.append(f"image 0x{len(data):x} bytes overruns 0x{limit:x}")
    sp, pc = struct.unpack_from("<II", data)
    ram_lo = int(device["sram_exec"][0]["origin"], 0)
    ram_hi = ram_lo + parse_size(device["sram_exec"][0]["length"])
    if sp % 8:
        problems.append(f"initial MSP 0x{sp:08x} not 8-byte aligned")
    if not ram_lo < sp <= ram_hi:
        problems.append(f"initial MSP 0x{sp:08x} outside RAM [0x{ram_lo:08x},0x{ram_hi:08x}]")
    align = int(device.get("vector_align", 4))
    if base % align:
        problems.append(f"image base 0x{base:08x} not {align}-byte aligned")
    if not pc & 1:
        problems.append(f"reset vector 0x{pc:08x} has no Thumb bit")
    entry = pc & ~1
    if not (base <= entry < base + len(data)):
        problems.append(
            f"reset vector 0x{pc:08x} outside this image [0x{base:08x},0x{base + len(data):08x}): "
            f"wrong APP_TARGET for this binary"
        )
    return problems


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_devices(args) -> int:
    for name, dev in sorted(load_devices().items()):
        exec_r = ", ".join(f"{r['name']} {r['origin']}+{r['length']}" for r in dev["sram_exec"])
        print(f"{name:20} flash {dev['flash']['origin']}+{dev['flash']['length']:8} "
              f"exec [{exec_r}] reserve {dev['rom_reserve_bytes']} align {dev['vector_align']}")
    return 0


def cmd_gen(args) -> int:
    project = pathlib.Path(args.project).resolve()
    devices = load_devices()
    device_name = args.device or detect_device(project)
    if not device_name:
        print("error: pass --device (no Mcu.CPN found in the project's .ioc)", file=sys.stderr)
        return 2
    if device_name not in devices:
        print(f"error: {device_name} is not in devices.json (see --devices)", file=sys.stderr)
        return 2
    device = devices[device_name]

    candidates = sorted(project.glob("*_FLASH.ld")) or sorted(project.glob("*.ld"))
    if not candidates:
        print("error: no linker script in project root", file=sys.stderr)
        return 2
    flash_ld = candidates[0]
    src = flash_ld.read_text()

    reserve = parse_size(args.reserve) if args.reserve else None
    ram_text, meta = build_ram_script(src, device, reserve)
    ram_ld = flash_ld.with_name(flash_ld.name.replace("_FLASH", "_RAM"))
    ram_ld.write_text(ram_text)

    guarded = patch_vtor_guard(project, device)
    if guarded:
        print(f"vtor guard  {guarded}")

    cmake_dir = project / "cmake"
    cmake_dir.mkdir(exist_ok=True)
    include = cmake_dir / "ram-target.cmake"
    include.write_text(emit_cmake(device_name, device, meta, pathlib.Path(__file__).resolve(),
                                  flash_ld.name, ram_ld.name, args.default_target))

    print(f"device      {device_name}")
    print(f"ram script  {ram_ld.relative_to(project)}  base=0x{meta['base']:08x} "
          f"window={meta['length']} reserve=0x{meta['reserve']:x} align={meta['align']}")
    print(f"cmake       {include.relative_to(project)} (default APP_TARGET={args.default_target})")
    print(f"next        add this line after add_subdirectory(...) in CMakeLists.txt:")
    print(f"              include(cmake/ram-target.cmake)")
    return 0


def cmd_verify(args) -> int:
    devices = load_devices()
    device = devices[args.device]
    problems = verify_image(pathlib.Path(args.bin), args.target, device)
    if problems:
        for p in problems:
            print(f"FAIL {args.bin}: {p}", file=sys.stderr)
        return 1
    print(f"OK   {args.bin} ({args.target})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("devices", help="list supported devices")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("gen", help="generate the RAM linker script and CMake include")
    p.add_argument("project")
    p.add_argument("--device", help="part number (default: Mcu.CPN from the .ioc)")
    p.add_argument("--reserve", help="bytes reserved at the start of executable SRAM (default: from devices.json)")
    p.add_argument("--default-target", choices=("flash", "ram"), default="flash")
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("verify", help="check an image the way a loader would")
    p.add_argument("--bin", required=True)
    p.add_argument("--target", required=True, choices=("flash", "ram"))
    p.add_argument("--device", required=True)
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
