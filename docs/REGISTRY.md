# Registry and driver contract

Descriptors are version 1 JSON objects documented in `schemas/`. `tools/rmb.py`
discovers `backends/*/backend.json` and `boards/*/board.json`; templates do not
match these roots. Identifiers must match directory names. Core code contains no
backend switch. Capabilities are vocabulary-checked and board requirements must
be a subset of the backend's capabilities. Paths may not escape their root.

Each backend owns its Python build driver. The CLI runs it using the current
Python interpreter with positional `configure` or `build`, and required
`--root`, `--backend`, `--board`, `--build-dir`, `--profile` arguments. An optional
`--user-config` is an absolute path to a private defaults file. Exit status is
propagated. New ports must implement this interface, descriptor and board tests,
not add a dispatch branch to the CLI. The ESP-IDF driver checks SDK version and
uses IDF's toolchain validation. Activate the pinned SDK environment first.

Use separate build directories per profile. `safe` disables the service;
`compile-enabled` uses public placeholders to test enabled linkage without
network or hardware. Configuration changes require a fresh build directory.
Private configuration belongs in ignored `user-config/`, never in board JSON.

Run `python3 tests/test_registry.py` to test discovery, extension without a
core switch, incompatible/invalid descriptors, traversal and CLI failure.
Schema files also support external JSON-schema tooling; runtime validation
is deliberately dependency-free and uses explicit errors, never assertions
(which Python optimization can remove). Required fields, nested SDK/toolchain
requirements, string/list/object types, duplicate and unknown capabilities,
and integer pins are checked. Booleans are not accepted as integers. Descriptor
paths must be nonempty relative paths without `..`; absolute paths and symlink
escapes are rejected. Drivers are confined to their backend directory;
implementation/default paths are confined to the repository. Listing through
the CLI also checks board/backend references and capability compatibility.

## Current ESP-IDF board limitation

The actual ESP-IDF driver is `backends/esp-idf/build.py` (the descriptor's
`driver` field), not `driver.py`. It currently supports **only**
`esp32c3-stm32f103`, target `esp32c3`, target device `stm32f103c8`.
Its `pins` exactly match the fixed board: UART TX=3/RX=4, BOOT0=5,
NRST=6, BOOT1=-1 (undriven, target PB2 externally low), SPI
SCLK=7/MOSI=1/MISO=0/CS=10. Changed pins/target/device are rejected.
These are validation metadata, not pin-generation support: only this fixed
board is supported. The compile-enabled Kconfig defaults match UART/BOOT/reset;
the safe profile keeps physical/network services disabled. See [wiring](WIRING.md).
A new wiring layout needs an explicit backend implementation change and tests,
not only a new JSON file. Custom backend directories remain independently discoverable.
