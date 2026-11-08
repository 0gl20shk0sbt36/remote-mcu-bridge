# WIP: resident port layer (branch `refactor/resident-port-layer`)

State: the ESP-IDF firmware builds and links with the new port layer; the native
fixture migration is incomplete, so this branch is **not** merged into the
verified main line.

Done
- `include/rmb/port.h`: the complete platform contract (time, logging, reset and
  reset cause, critical section, tasks, mutex/semaphore/queue/event, watchdog,
  retained RAM, business-slot storage and executable mapping, base identity,
  network bring-up). A new host implements these and nothing else.
- `esp32_rom/main/platform/`: the only directory that may name platform APIs —
  `esp_idf_port.c`, `net_esp_idf.c` (Wi-Fi/NVS/netif/discovery), `entry_esp_idf.c`.
- `esp32_rom/main/resident.c` (was `service.c`), `streams.inc`,
  `supervisor.inc`, `service_watchdog.inc`, `module_runtime.c`,
  `generic_network.c`: portable, no ESP identifiers; `module_runtime.h` returns
  `int` instead of `esp_err_t`.
- Reset-cause values stay ESP-compatible (1 power-on, 3 software, 4 panic,
  5 interrupt watchdog, 6 task watchdog) because they are persisted in the fault
  journal and reported to clients.
- `compile-enabled` ESP-IDF build configures, compiles and links.

Remaining work (native fixtures)
- `esp32_rom/tests/port_native.h` + `port_native_impl.c` provide a pthread-based
  port with instrumentation, but the extraction fixtures still need their
  hand-written SDK stubs replaced and their assertions re-pointed at the port
  (instrumentation counters, reset enum, slot hooks).
- `test_module_format.py` must assert the port call (`rmb_port_slot_map`) where it
  asserted `ESP_PARTITION_MMAP_INST`.
- Fixture include paths differ by location (`ROOT` is `esp32_rom` for
  `esp32_rom/tests/*`, the repository root for `tests/*`).

Definition of done: `ctest --preset native` green (30/30), both ESP profiles
build, default and optional service modules still link, then merge.
