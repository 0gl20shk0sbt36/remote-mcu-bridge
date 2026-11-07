# Module build and link checks

The module builder links real ESP-IDF objects against the **exact final resident
ELF** using `--just-symbols`. Rebuild the module whenever the resident ELF changes.
ESP-IDF 5.5.5, its Python dependencies, and the ESP32-C3 RISC-V toolchain are required.
No hardware is needed for a link check; a successful link is not a runtime test.

## Environment

Set `IDF_PATH` to your installed ESP-IDF checkout. Put the installed toolchain's
`bin` directory on `PATH` and use the Python environment containing IDF dependencies.
There are no developer-specific SDK/toolchain paths and the module scripts do not
source `export.sh` or download dependencies. `--help` works without an SDK.

Tool prefix precedence: explicit `--tool-prefix`, then `RISCV_TOOL_PREFIX`, then
`CROSS_COMPILE`, then `riscv32-esp-elf-` resolved from `PATH`. An absolute prefix is
also accepted. `IDF_PATH` is required; missing SDK/tool executables produce an
argument error before compilation. Resident configuration/builds use `tools/rmb.py`.

## Hardware-free compilecheck

First configure and build the enabled resident (the safe profile intentionally
omits the module runtime):

```sh
python tools/rmb.py configure --profile compile-enabled
python tools/rmb.py build --profile compile-enabled
python tools/check_module_link.py --base-elf "$BUILD_DIR/$RESIDENT_ELF"
```

Set `BUILD_DIR` to `out/esp-idf/esp32c3-stm32f103/compile-enabled` and
`RESIDENT_ELF` to the actual top-level resident ELF filename. The helper reads the
**real ELF with `nm`**, checks its 32768-byte `module_data_reserve`, derives a
synthetic page-aligned text VA above resident IROM symbols, and passes the real
resident data address. It then invokes the real module compiler/linker, checks the
serialized header, resident hash, unresolved imports and duplicate definitions.
It does not synthesize an ELF, replace imports, or stub driver functions.

Outputs go to `module-compilecheck/` beside the ELF (or `--output-dir`):
`business.NOT-DEPLOYABLE.bin`, `NOT-DEPLOYABLE.txt`, `compilecheck.json` with
`deployable: false`, and `module-link/` containing the ELF, map, command log,
relocations and link proof. These are **NOT DEPLOYABLE**: the synthetic text VA
is not a target-reported mapping. Never upload or flash compilecheck outputs.
The CI enabled matrix profile runs this check after its resident build. The safe
profile only builds the resident. CI never flashes or connects to hardware.

## Deployment-oriented module build

Use only the final deployed resident ELF and actual target-reported text mapping:

```sh
python esp32_rom/module/build_module.py \
  --base-elf "$BUILD_DIR/$RESIDENT_ELF" \
  --text-address "$TARGET_TEXT_ADDRESS" \
  --data-address "$TARGET_DATA_ADDRESS" \
  --output "$OUTPUT_IMAGE"
```

The compile database defaults to `compile_commands.json` beside the ELF; override
with `--compile-commands` when necessary. Data VA must match the ELF reserve.
This command does not deploy anything. Compilecheck success alone does not prove
hardware behavior, timing, mapping validity, or safety of a deployment.
