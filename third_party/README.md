# Dependency sources and license records

This directory contains pins, provenance and verbatim license texts, **not SDK source trees**.
`manifest.json` covers the ESP32-C3 `esp32_rom` and STM32F103 `app_cubemx_f103`
targets. STM32F4 and unrelated examples are not included.

## Source bootstrap (Python 3.12+ and Git)

From the repository root:

```sh
python3 tools/bootstrap_deps.py --help
python3 tools/bootstrap_deps.py --validate-manifest
python3 tools/bootstrap_deps.py
```

The last command fetches exact detached Git commits and initializes ESP-IDF's
recursive submodules at their SDK gitlinks. It fetches only CubeF1 plus the two
explicit HAL/device dependencies for STM32; no unused Cube BSP/middleware
submodules are initialized. Source downloads are not global installs. Default
locations are `.deps/esp-idf` and `vendor/STM32CubeF1`. Set `IDF_PATH` and/or
`STM32CUBE_F1_PATH` to override source destinations or reuse existing **clean,
pinned Git checkouts**. Existing non-Git vendor directories are deliberately
rejected rather than silently trusted or overwritten. Failed first acquisitions
are staged and cleaned up; existing directories are never removed/replaced.

`--deps-dir` / `MCU_BRIDGE_DEPS_DIR` overrides the IDF destination (Cube keeps its
CMake-compatible default; override Cube with `STM32CUBE_F1_PATH`).
`--only idf` and `--only stm32` limit operations. The script prints shell export
lines; review and apply them manually (do not `eval` network command output).
`--check` is read-only and verifies the selected existing Git SDK(s), pins and
recursive IDF gitlink initialization. It does not install tools or build firmware.

### Offline use and cache

```sh
export IDF_PATH="$PWD/.deps/esp-idf"
export STM32CUBE_F1_PATH="$PWD/vendor/STM32CubeF1"
export MCU_BRIDGE_DEPS_CACHE="$PWD/.deps/cache"
python3 tools/bootstrap_deps.py --offline
```

For IDF offline, supply an already populated Git checkout with **all recursive
submodules** via `IDF_PATH`; a top-level archive alone is insufficient. For STM32,
use existing pinned Git sources, or put these original **unprefixed git-archive**
files in the cache (their expected SHA-256 digests are in the manifest):

* `STM32CubeF1-d12e752.tar.gz`
* `stm32f1xx_hal_driver-fee494a92b5ad331f92ad21f76c66a5cb83773ee.tar.gz`
* `cmsis_device_f1-c8e9a4a4f16b6d2cb2a2083cbe5161025280fb22.tar.gz`

These digests identify the original locally available archives, **not** GitHub's
automatically generated release tarballs. The script never downloads an archive
and pretends it has the same hash. Archive extraction rejects links and uses
Python's data extraction filter. An inventory marker allows verified reuse of
archive-created sources and detects file changes. `--check` requires Git instead;
use `--offline` to revalidate archive inventories. Alternatively, the cache can
hold Git repositories named `STM32CubeF1.git`, `hal.git`, and `cmsis_device.git`
containing the exact commits. No cache means a clear offline failure, not a
fallback to network. `--cache` overrides the cache environment variable.

### Toolchain installation remains explicit

The bootstrap does not run pip, install.sh, apt, or any global package manager.
For ESP-IDF, select a project-local tool cache before using upstream installers:

```sh
export IDF_TOOLS_PATH="$PWD/.deps/idf-tools"
"$IDF_PATH/install.sh" esp32c3
. "$IDF_PATH/export.sh"
```

That **separate** step needs network or a prepopulated upstream tool/Python cache.
For offline builds, set `IDF_TOOLS_PATH` to an existing compatible tool cache and
activate the matching IDF Python environment. STM32 needs an installed
`arm-none-eabi` GCC toolchain; select it through CMake/PATH. No compiler binaries
are redistributed here.

## Pin/provenance limitations

* ESP-IDF v5.5.5: local exact Git tag and HEAD verified; cJSON 1.7.19 commit
  independently read from the pinned IDF gitlink and `.gitmodules` SBOM metadata.
* CubeF1: local `package.xml` reports patch `FW.F1.1.8.7`; its generated
  `version-license.txt` still says 1.8.6. This discrepancy is recorded, not hidden.
  The Cube/HAL/device commits and archive digests come from the original dependency
  manifest's local git-archive PAX provenance. No upstream tag lookup/download was
  performed for this staging work. HAL/device gitlink relationships to the parent
  Cube commit remain **unverified**. Bootstrap deliberately uses the explicit
  component pins; do not describe them as proven parent gitlinks.

## Integration required outside this directory

* Keep `.deps/` and downloaded `vendor/` SDK sources out of Git; add ignore rules.
* Keep CMake's `IDF_PATH` and `STM32CUBE_F1_PATH` overrides. Original STM32 CMake
  already supports the latter and defaults to `vendor/STM32CubeF1`.
* Include `licenses/`, this manifest, and the notices below in source releases;
  include relevant license/attribution material with binary releases too.
* Preserve generated STM headers and resolve their component-root `LICENSE`
  references before publication. See `NOTICES.md`: the records here do not grant
  a new license to generated files or cure a missing original license grant.
* Produce a linked-firmware license/SBOM inventory for release configurations.
  ESP-IDF's transitive components (including mbedTLS, FreeRTOS, lwIP, newlib and
  Espressif binary libraries as applicable) retain their own licenses. The IDF
  core Apache license and explicit cJSON record are not a complete binary SBOM.
