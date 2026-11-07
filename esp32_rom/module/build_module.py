#!/usr/bin/env python3
"""Link ABI-3 business code against the exact, final resident ESP-IDF ELF.

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
ABI = 3
DRAM_SIZE = 32768
HEADER = struct.Struct('<10I')
# Never extract another copy of any of these process-wide runtimes.
EXCLUDED_COMPONENTS = {'main', 'freertos', 'newlib', 'heap', 'pthread', 'cxx',
                       'riscv', 'esp_system', 'bootloader_support',
                       'bootloader', 'unity', 'cmock'}


def validate_image(image):
    if len(image) < HEADER.size:
        raise ValueError('short header')
    h = HEADER.unpack_from(image)
    magic, abi, hs, total, ta, ts, da, ds, bs, entry = h
    if (magic != MAGIC or abi != ABI or hs != 40 or total != len(image)
            or total > 0x100000 - 4096 or not 0x42000000 <= ta < 0x42800000
            or ta % 65536 != 40 or ts > 0x42800000 - ta or not ts
            or ts % 4 or ts > 0x100000 - 4096 - 40 or ds > DRAM_SIZE
            or bs > DRAM_SIZE - ds or ts + ds != total - 40
            or not ta <= entry < ta + ts or entry % 2):
        raise ValueError('invalid module layout')
    return h


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-elf', required=True)
    ap.add_argument('--compile-commands', help='defaults to compile_commands.json beside base ELF')
    ap.add_argument('--text-address', required=True, type=lambda x: int(x, 0),
                    help='actual module_runtime_text_address from target status; never guessed')
    ap.add_argument('--data-address', type=lambda x: int(x, 0),
                    help='optional target-reported address; checked against resident ELF')
    ap.add_argument('--output', required=True)
    ap.add_argument('--tool-prefix', default=os.environ.get('RISCV_TOOL_PREFIX',
                    os.environ.get('CROSS_COMPILE', 'riscv32-esp-elf-')),
                    help='tool path/name prefix (default: RISCV_TOOL_PREFIX, CROSS_COMPILE, or riscv32-esp-elf- on PATH)')
    a = ap.parse_args()
    idf_path = os.environ.get('IDF_PATH')
    if not idf_path or not (pathlib.Path(idf_path) / 'tools/idf.py').is_file():
        ap.error('IDF_PATH must point to an installed ESP-IDF checkout (tools/idf.py required)')
    missing = [a.tool_prefix + tool for tool in ('gcc', 'nm', 'objcopy', 'objdump', 'readelf', 'ar')
               if shutil.which(a.tool_prefix + tool) is None]
    if missing:
        ap.error('missing tool(s): ' + ', '.join(missing) +
                 '; set PATH, RISCV_TOOL_PREFIX, or --tool-prefix')
    here = pathlib.Path(__file__).resolve().parent
    root = here.parent.parent
    base_elf = pathlib.Path(a.base_elf).resolve()
    build = base_elf.parent
    database = pathlib.Path(a.compile_commands).resolve() if a.compile_commands else build / 'compile_commands.json'
    out = pathlib.Path(a.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / 'module-link'
    work.mkdir(exist_ok=True)
    commands = []

    def run(tool, *args, cwd=None):
        cmd = [a.tool_prefix + tool, *map(str, args)]
        commands.append(cmd)
        return subprocess.check_output(cmd, text=True, cwd=cwd)

    def syms(elf):
        result = {}
        for line in run('nm', '-g', '--defined-only', elf).splitlines():
            fields = line.split()
            if len(fields) == 3:
                result[fields[2]] = (int(fields[0], 16), fields[1])
        return result

    base_hash = hashlib.sha256(base_elf.read_bytes()).hexdigest()
    base = syms(base_elf)
    ta = a.text_address
    if 'module_data_reserve' not in base:
        raise ValueError('base ELF lacks module_data_reserve; build the compile-enabled profile')
    da = base['module_data_reserve'][0]
    reserve = re.search(r'^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+\w\s+module_data_reserve$',
                        run('nm', '-S', '--defined-only', base_elf), re.M)
    if not reserve or int(reserve[2], 16) != DRAM_SIZE:
        raise ValueError(f'base ELF must reserve module_data_reserve[{DRAM_SIZE}] for ABI-{ABI}')
    if not 0x42000000 <= ta < 0x42800000 or ta % 65536 != HEADER.size:
        raise ValueError('text address must be target IROM mapping base + 40-byte header')
    if a.data_address is not None and a.data_address != da:
        raise ValueError('target data address differs from resident ELF')

    records = json.loads(database.read_text())
    template = next((r for r in records if pathlib.Path(r['file']).name == 'service.c'), None)
    if template is None:
        raise ValueError('compile_commands.json lacks resident service.c compile configuration')
    original = template.get('arguments') or shlex.split(template['command'])
    # Reuse the real generated sdkconfig, component include paths, defines,
    # toolchain response file and target flags. Replace only input/output.
    flags = []
    i = 1
    while i < len(original):
        arg = original[i]
        if arg in ('-o', '-MF', '-MT', '-MQ'):
            i += 2
            continue
        if arg in ('-c', '-MD', '-MMD', '-MP') or arg == template['file']:
            i += 1
            continue
        flags.append(arg)
        i += 1
    flags += ['-I' + str(root / 'rom'), '-I' + str(here.parent / 'main'),
              '-I' + str(here), '-Os', '-msmall-data-limit=0', '-mno-relax',
              '-ffreestanding', '-fno-builtin', '-fno-common',
              '-ffunction-sections', '-fdata-sections']
    macros = run('gcc', *flags, '-dM', '-E', '-include', 'module_abi.h',
                 '-x', 'c', '/dev/null', cwd=template['directory'])
    for name, expected in (('MODULE_ABI_VERSION', ABI), ('MODULE_DRAM_SIZE', DRAM_SIZE)):
        value = re.search(r'^#define ' + name + r'\s+(\d+)[uUlL]*\s*$', macros, re.M)
        if not value or int(value[1]) != expected:
            raise ValueError(f'{name} must be {expected} in the actual IDF compile configuration')
    sources = [root / 'rom/rom.c', root / 'rom/devices.c', here / 'entry.c', here / 'business.c',
               here / 'logic/spi_diagnostic.c', here / 'platform/esp_idf_io.c']
    objs = []
    for n, source in enumerate(sources):
        if not source.is_file():
            raise ValueError('missing required module source: ' + str(source))
        obj = work / f'{n}-{source.stem}.o'
        run('gcc', *flags, '-c', source, '-o', obj, cwd=template['directory'])
        objs.append(obj)

    ld = work / 'module.ld'
    ld.write_text(f'''ENTRY(module_entry)
MEMORY {{ CODE (rx) : ORIGIN = 0x{ta:x}, LENGTH = {0x100000 - 4096 - 40}
 DATA (rw) : ORIGIN = 0x{da:x}, LENGTH = {DRAM_SIZE} }}
SECTIONS {{
 .text : {{ *(.literal .literal.* .text .text.* .iram .iram.* .iram0.literal .iram0.text .iram1 .iram1.*) . = ALIGN(4); }} > CODE
 .data : {{ KEEP(*(.module_base_id)) *(.rodata .rodata.* .srodata .srodata.* .data .data.* .sdata .sdata.* .dram .dram.* .dram0.data .dram1 .dram1.* .sdata2 .sdata2.* .got .got.*) . = ALIGN(4); }} > DATA
 .bss (NOLOAD) : {{ *(.bss .bss.* .sbss .sbss.* .sbss2 .sbss2.* .noinit .noinit.* COMMON) . = ALIGN(4); }} > DATA
 /DISCARD/ : {{ *(.comment .eh_frame .eh_frame.* .riscv.attributes .note.GNU-stack) }}
}}
''')
    archives = sorted(p for p in (build / 'esp-idf').rglob('*.a')
                      if p.relative_to(build / 'esp-idf').parts[0] not in EXCLUDED_COMPONENTS)
    if not archives:
        raise ValueError('no built IDF static archives beside base ELF')
    # Console UART0 VFS can retain IDF uart APIs in the base. Never mix those
    # APIs' private p_uart_obj with the independently linked UART1 driver's
    # state. Namespace ALL public uart.c APIs and every module reference.
    uart_archive = build / 'esp-idf/esp_driver_uart/libesp_driver_uart.a'
    uart_names = sorted({line.split()[-1] for line in run('nm', '-g', '--defined-only', uart_archive).splitlines()
                         if len(line.split()) == 3 and line.split()[-1].startswith('uart_')})
    rename = work / 'uart-symbols.txt'
    rename.write_text(''.join(name + ' business_' + name + '\n' for name in uart_names))
    for obj in objs:
        run('objcopy', '--redefine-syms=' + str(rename), obj)
    namespaced_uart = work / 'libbusiness_uart.a'
    run('objcopy', '--redefine-syms=' + str(rename), uart_archive, namespaced_uart)
    archives = [namespaced_uart if p == uart_archive else p for p in archives]
    # Own the entire GP-SPI driver's state, even if future bases retain an API.
    # SPI flash uses its separate resident spi_flash driver, never these APIs.
    spi_archive = build / 'esp-idf/esp_driver_spi/libesp_driver_spi.a'
    spi_names = sorted({line.split()[-1] for line in run('nm', '-g', '--defined-only', spi_archive).splitlines()
                        if len(line.split()) == 3})
    spi_rename = work / 'spi-symbols.txt'
    spi_rename.write_text(''.join(name + ' business_' + name + '\n' for name in spi_names))
    for obj in objs:
        run('objcopy', '--redefine-syms=' + str(spi_rename), obj)
    namespaced_spi = work / 'libbusiness_spi.a'
    run('objcopy', '--redefine-syms=' + str(spi_rename), spi_archive, namespaced_spi)
    archives = [namespaced_spi if p == spi_archive else p for p in archives]
    # C3 GP-SPI lock needs missing 32-bit compiler atomics. Extract ONLY this
    # stateless IDF object, never the newlib runtime/heap/locks archive as a whole.
    atomic_obj = work / 'stdatomic.c.obj'
    run('ar', 'x', build / 'esp-idf/newlib/libnewlib.a', 'stdatomic.c.obj', cwd=work)
    atomic_symbols = syms(atomic_obj)
    if any(kind.upper() in ('B', 'D', 'S', 'G', 'C') for _, kind in atomic_symbols.values()):
        raise RuntimeError('atomic helper unexpectedly contains process-wide mutable state')
    atomic_archive = work / 'libbusiness_atomics.a'
    run('ar', 'rcs', atomic_archive, atomic_obj)
    archives.append(atomic_archive)
    elf = work / 'business.elf'
    # FIRST definitions are resident absolutes. Some IDF archives package many
    # API functions into one object: allow overlapping definitions, then GC the
    # unused copies. Audit below requires every resident global to stay absolute.
    link_flags = ['-nostdlib', '-nostartfiles', '-march=rv32imc_zicsr_zifencei',
                  '-mabi=ilp32', '-mno-relax', '-Wl,--no-relax',
                  '-Wl,--gc-sections', '-Wl,--allow-multiple-definition',
                  '-Wl,--emit-relocs', '-Wl,--just-symbols=' + str(base_elf),
                  '-Wl,-Map=' + str(work / 'business.map'), '-T', str(ld)]
    run('gcc', *link_flags, '-o', elf, *objs, '-Wl,--start-group', *archives, '-Wl,--end-group')
    undef = run('nm', '-u', elf).strip()
    if undef:
        raise RuntimeError('undefined imports: ' + undef)
    symbols = syms(elf)
    spi_ownership = {name: {'address': hex(address), 'kind': kind}
                     for name, (address, kind) in symbols.items()
                     if name.startswith(('business_spi_', 'business_spicommon_', 'spi_hal_', 'spi_bus_lock_'))}
    for name in ('spi_bus_initialize', 'spi_bus_add_device', 'spi_device_queue_trans',
                 'spi_device_get_trans_result', 'spi_bus_remove_device', 'spi_bus_free'):
        address, kind = symbols['business_' + name]
        if kind.upper() != 'T' or not ta <= address < ta + 0x100000 - 4096 - 40:
            raise RuntimeError('SPI API is not module-owned: ' + name)
    duplicates = [name for name in symbols if name in base and symbols[name][0] != base[name][0]]
    if duplicates:
        raise RuntimeError('resident runtime/global definitions copied into module: ' + ', '.join(duplicates))
    sections = run('objdump', '-h', elf)
    (work / 'sections.txt').write_text(sections)
    (work / 'relocations.txt').write_text(run('readelf', '-rW', elf))
    # Allocatable orphan sections would not be serialized by the image format.
    rows = sections.splitlines()
    for n, row in enumerate(rows[:-1]):
        m = re.match(r'\s*\d+\s+(\S+)\s+([0-9a-fA-F]+)\s+', row)
        if m and int(m[2], 16) and 'ALLOC' in rows[n + 1] and m[1] not in ('.text', '.data', '.bss'):
            raise RuntimeError('unserialized allocatable section: ' + m[1])
    relocs = (work / 'relocations.txt').read_text()
    # Archive objects may retain R_RISCV_RELAX *annotations*. They are not
    # applied because the final link uses --no-relax; GP-relative code is not OK.
    if re.search(r'R_RISCV_GPREL', relocs):
        raise RuntimeError('module contains forbidden GP-relative relocations')

    def section(name):
        path = work / (name + '.bin')
        run('objcopy', '-O', 'binary', '--only-section=.' + name, elf, path)
        return path.read_bytes()

    text = section('text')
    data = bytes.fromhex(base_hash) + section('data')[32:]
    match = re.search(r'\s\.bss\s+([0-9a-fA-F]+)\s', sections)
    bss = int(match[1], 16) if match else 0
    entry = symbols['module_entry'][0]
    header = HEADER.pack(MAGIC, ABI, HEADER.size, HEADER.size + len(text) + len(data),
                         ta, len(text), da, len(data), bss, entry)
    image = header + text + data
    validate_image(image)
    if hashlib.sha256(base_elf.read_bytes()).hexdigest() != base_hash:
        raise RuntimeError('base ELF changed during module build; rerun against final ELF')
    out.write_bytes(image)
    link_map = (work / 'business.map').read_text()
    extracted = sorted(set(re.findall(r'([^\s]+\.a)\(([^)]+)\)', link_map)))
    report = {'artifact': str(out), 'base_elf': str(base_elf), 'base_sha256': base_hash,
              'compile_commands': str(database), 'text_address': hex(ta), 'text_bytes': len(text),
              'data_address': hex(da), 'data_bytes': len(data), 'bss_bytes': bss,
              'dram_capacity': DRAM_SIZE, 'entry': hex(entry), 'abi': ABI,
              'undefined_symbols': undef, 'duplicate_resident_definitions': duplicates,
              'gp_relaxation': False, 'header_bytes': HEADER.size,
              'spi_link_ownership': spi_ownership,
              'sources': list(map(str, sources)), 'link_flags': link_flags,
              'excluded_components': sorted(EXCLUDED_COMPONENTS),
              'extracted_archive_members': [list(x) for x in extracted]}
    (work / 'commands.json').write_text(json.dumps(commands, indent=2))
    (work / 'proof.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
