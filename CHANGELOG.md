# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-11-08

First release of the ABI4 generic resident plus the portable service runtime.

### Added

- Generic resident contract `include/rmb/port.h` (46 functions) covering time,
  logging, reset, critical sections, tasks, synchronisation, watchdog, retained
  RAM, the business slot, executable mapping, base identity and network.
- ESP-IDF platform implementation in `esp32_rom/main/platform/`
  (`esp_idf_port.c`, `net_esp_idf.c`, `entry_esp_idf.c`).
- Machine-enforced boundary test `esp32_rom/tests/test_port_boundary.py` and the
  coupling measurement tool `tools/analyze_dependencies.py`.
- `test_build_config.py`, `test_cli_token_file.py` and `test_port_boundary.py`
  native suites.
- Documentation set: development, service authoring, default STM firmware,
  resident platform porting, cross-platform porting handbook, dependency
  analysis, testing and validation evidence index.
- Deterministic release builder `tools/make_release.py`.

### Changed

- Resident logic is platform-free: `resident.c` (formerly `service.c`),
  `generic_network.c`, `module_runtime.c`, `streams.inc`, `supervisor.inc` and
  `service_watchdog.inc` no longer name platform APIs.
- Compile-time configuration lives in the single tracked file
  `config/build.defaults`; network credentials are supplied at build time with
  `--credentials-file` and overridden by a generated 0600 overlay.
- Board support is registry-driven: a board is a JSON descriptor, and the driver
  verifies every pin and the target device against the compiled image.
- The default built-in service is `stm-uart` without SPI handlers, SPI
  diagnostics or the GP-SPI driver; SPI requires `--builtin-service stm-spi`.
- Source formatting is pinned (clang-format 23.1.5 / Ruff 0.17.4) and applied by
  `tools/format_code.py`; extraction fixtures compare tokens rather than exact
  whitespace.
- Watchdog leases, RTC fault journal and sequence-bound acknowledgement keep the
  ESP reset-reason numbering (1/3/4/5/6/7) since values are persisted and
  reported to clients.

### Removed

- ABI3 `module_ops` callbacks, numbered dispatch, resident ROM/staging tables and
  the obsolete `main/uart_{framed,priority}.inc` copies.
- Split compile-time defaults (`esp32_rom/sdkconfig.defaults`,
  `config/ci-{enabled,disabled}.defaults`).
- The stale `export-manifest.json` and the in-tree delivery inventory; release
  manifests are now generated into the archive only.

### Verified

- Native suite: 31/31 (needs `IDF_PATH` so fixtures can find cJSON).
- ESP-IDF `safe` and `compile-enabled` builds; default and optional service
  modules cross-linked against the final base.
- Hardware acceptance of both the ABI4 refactor and the port-layer firmware:
  Wi-Fi module replacement, five fault modes with RTC reports, STM SRAM/UART,
  and SPI diagnostics as an optional service. See `docs/validation/`.

### Known limits

- STM Flash erase/write is exercised outside the automated flow; no physical
  power-cut test, no logic-analyzer timing evidence, one bench unit.
- Modules share the resident address space: this is not a sandbox, and a module
  change requires rebuilding services against the exact running base.
- The SPI all-FF root cause from the ABI3 era remains unproven.
