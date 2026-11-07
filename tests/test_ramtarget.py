#!/usr/bin/env python3
"""Unit tests for tools/ramtarget: linker transform, VTOR defines, verification.

Run: python3 tests/test_ramtarget.py
The synthetic linker scripts deliberately use different region names than the
STM32CubeF4 one, to prove the transform is not tied to one vendor's script.
"""

import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools" / "ramtarget"))
import ramtarget as rt  # noqa: E402

GENERIC_SCRIPT = """
MEMORY
{
ROM (rx)  : ORIGIN = 0x08000000, LENGTH = 256K
SRAM (xrw): ORIGIN = 0x20000000, LENGTH = 64K
SRAM2(xrw): ORIGIN = 0x10000000, LENGTH = 32K
}
_estack = ORIGIN(SRAM) + LENGTH(SRAM);
SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } >ROM
  .text       : { *(.text*) } >ROM
  .rodata     : { *(.rodata*) } >ROM
  .data       : { *(.data*) } >SRAM AT> ROM
  .bss        : { *(.bss*) } >SRAM
}
"""

GENERIC_DEVICE = {
    "flash": {"origin": "0x08000000", "length": "256K"},
    "sram_exec": [{"name": "SRAM", "origin": "0x20000000", "length": "64K"}],
    "sram_data_only": [{"name": "SRAM2", "origin": "0x10000000", "length": "32K"}],
    "rom_reserve_bytes": 8192,
    "vector_align": 512,
    "vtor": {"type": "stm32_vect_tab", "sram_base": "0x20000000"},
    "kernel_object_libs": ["Some_Driver_Lib"],
}

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# --------------------------------------------------------------------------- #
# sizes
# --------------------------------------------------------------------------- #
check("parse 1024K", rt.parse_size("1024K") == 1024 * 1024)
check("parse 0x4000", rt.parse_size("0x4000") == 16384)
check("parse 64M", rt.parse_size("64M") == 64 * 1024 * 1024)
check("fmt 114688 -> 112K", rt.fmt_size(114688) == "112K")

# --------------------------------------------------------------------------- #
# memory parsing and region detection on a non-CubeMX script
# --------------------------------------------------------------------------- #
regions = rt.parse_memory(GENERIC_SCRIPT)
check("regions parsed", set(regions) == {"ROM", "SRAM", "SRAM2"}, str(set(regions)))
check("flash region by origin", rt.pick_flash_region(regions, GENERIC_DEVICE) == "ROM")
check("exec region by origin", rt.pick_exec_region(regions, GENERIC_DEVICE) == "SRAM")

# --------------------------------------------------------------------------- #
# transform
# --------------------------------------------------------------------------- #
out, meta = rt.build_ram_script(GENERIC_SCRIPT, GENERIC_DEVICE)
check("base after reserve", meta["base"] == 0x20002000, hex(meta["base"]))
check("window shrinks", meta["length"] == 64 * 1024 - 8192, str(meta["length"]))
check("SRAM region rewritten", "ORIGIN = 0x20002000" in out and "LENGTH = 56K" in out, out)
check("no flash-resident sections left", ">ROM" not in out and "AT> ROM" not in out, out)
check("data loads from SRAM too", "AT> SRAM" in out)
check("vector assert emitted", "ASSERT(ORIGIN(SRAM) == 0x20002000" in out)
check("limit symbol emitted", "_ram_image_limit" in out)
check("CCM-like region untouched", rt.parse_memory(out)["SRAM2"]["origin"] == 0x10000000)

# region-name prefix collision (CCMRAM must not be mistaken for RAM)
cube_like = GENERIC_SCRIPT.replace("SRAM2(xrw)", "CCMSRAM(xrw)").replace(">SRAM ", ">RAM ")
dev = dict(GENERIC_DEVICE, sram_exec=[{"name": "RAM", "origin": "0x20000000", "length": "64K"}])
out2, meta2 = rt.build_ram_script(cube_like.replace("SRAM (xrw)", "RAM  (xrw)"), dev)
check("RAM/CCMRAM not confused", meta2["base"] == 0x20002000 and "ORIGIN = 0x10000000" in out2)

# reserve that breaks alignment is rejected
try:
    rt.build_ram_script(GENERIC_SCRIPT, dict(GENERIC_DEVICE, rom_reserve_bytes=8191))
    check("misaligned reserve rejected", False)
except ValueError:
    check("misaligned reserve rejected", True)

# --------------------------------------------------------------------------- #
# VTOR defines
# --------------------------------------------------------------------------- #
defs = rt.vtor_defines(GENERIC_DEVICE, meta)
check("vtor offset 0x2000", "VECT_TAB_OFFSET=0x2000U" in defs, str(defs))
check("vtor sram macro", "VECT_TAB_SRAM" in defs and "USER_VECT_TAB_ADDRESS" in defs)

# --------------------------------------------------------------------------- #
# device inheritance
# --------------------------------------------------------------------------- #
devices = rt.load_devices()
check("device present", "STM32F407ZGT6" in devices)
check("inheritance merged", devices["STM32F405ZGT6"]["sram_exec"] == devices["STM32F407ZGT6"]["sram_exec"])
check("inherited reserve", devices["STM32F405ZGT6"]["rom_reserve_bytes"] == 16384)

# --------------------------------------------------------------------------- #
# image verification
# --------------------------------------------------------------------------- #
def make_image(sp, pc, size=64):
    body = struct.pack("<II", sp, pc) + b"\x00" * (size - 8)
    return body


import tempfile
import os

tmp = pathlib.Path(tempfile.mkdtemp(prefix="ramtarget-test-"))
img = tmp / "ok.bin"
img.write_bytes(make_image(0x20010000, 0x20002001))
check("good ram image accepted", rt.verify_image(img, "ram", GENERIC_DEVICE) == [], str(rt.verify_image(img, "ram", GENERIC_DEVICE)))

img.write_bytes(make_image(0x20010000, 0x08000001))
check("flash image rejected as ram", bool(rt.verify_image(img, "ram", GENERIC_DEVICE)))

img.write_bytes(make_image(0x2000FFF9, 0x20002000))  # inside RAM, misaligned
problems = rt.verify_image(img, "ram", GENERIC_DEVICE)
check("misaligned MSP + no thumb rejected", len(problems) == 2, str(problems))

img.write_bytes(make_image(0x30000000, 0x20002001))
check("MSP outside RAM rejected", bool(rt.verify_image(img, "ram", GENERIC_DEVICE)))

img.write_bytes(make_image(0x20010000, 0x20002001, size=64 * 1024))
check("oversized image rejected", bool(rt.verify_image(img, "ram", GENERIC_DEVICE)))

# --------------------------------------------------------------------------- #
# F1-style project (64 KiB Flash, 20 KiB SRAM, 1 KiB reserve)
# --------------------------------------------------------------------------- #
F1_SCRIPT = """
MEMORY
{
FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
RAM (xrw)   : ORIGIN = 0x20000000, LENGTH = 20K
}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } >FLASH
  .text : { *(.text*) } >FLASH
  .data : { *(.data*) } >RAM AT> FLASH
  .bss : { *(.bss*) } >RAM
}
"""
f1 = devices["STM32F103C8T6"]
out3, meta3 = rt.build_ram_script(F1_SCRIPT, f1)
check("f103 base 0x20000400", meta3["base"] == 0x20000400, hex(meta3["base"]))
check("f103 window 19K", meta3["length"] == 20 * 1024 - 1024, str(meta3["length"]))
check("f103 vector assert", "ASSERT(ORIGIN(RAM) == 0x20000400" in out3)
f1img = tmp / "f1.bin"
f1img.write_bytes(make_image(0x20004000, 0x20000409))
check("f103 ram image accepted", rt.verify_image(f1img, "ram", f1) == [], str(rt.verify_image(f1img, "ram", f1)))
f1img.write_bytes(make_image(0x20020000, 0x20000409))   # F407-sized stack on an F103
check("f103 rejects f407 stack", bool(rt.verify_image(f1img, "ram", f1)))

for f in tmp.iterdir():
    os.unlink(f)
os.rmdir(tmp)


print()
print(f"{len(failures)} failure(s)" if failures else "all checks passed")
sys.exit(1 if failures else 0)
