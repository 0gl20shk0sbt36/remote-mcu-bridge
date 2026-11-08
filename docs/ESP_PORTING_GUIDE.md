# ESP porting guide — ABI 4

The resident is now portable behind `include/rmb/port.h`; see [Resident platform porting](RESIDENT_PLATFORM.md) for the contract and the minimum a new host implements. This guide applies the [architecture](ARCHITECTURE.md) and [base/platform boundary](BASE_PLATFORM_BOUNDARY.md) to ESP-IDF. It is a migration/verification checklist; the exact fixed-board ABI4 results and limits are in the independent hardware report, not a promise for other ports. Older guides describing module_ops or numbered dispatch document ABI3 interfaces and are not the ABI4 service SDK.

## 1. Separate base from target service

The ESP resident owns network/auth, generic loader, module-slot storage/footer updates, generic jobs/WDT and recovery. Move STM ROM algorithms, target device/Flash/RAM geometry, firmware staging, BOOT/NRST, UART/SPI and pin setup into the STM module. Do not initialize target hardware or require a target device to host a non-ROM service.

Keep exact ELF hash/address imports and 32-byte binding layout, fixed partition/footer, data/BSS setup and MEMPROT+LOCK. ABI4 intentionally rejects old binaries. Do not weaken admission or remove lock checks to make an old module load.

## 2. Export only the generic service contract

Use `include/rmb/service.h` (only stdint.h/stddef.h/stdbool.h), `RMB_SERVICE_ABI=4u`, `MODULE_ABI_VERSION` alias. `module_entry_fn` is `int (*)(const rmb_host *)`. Host fields are `abi,size,net,now_us,progress,run`; net fields are `abi,size,listen,exact,read,write,authenticated`, in that order. Exact signatures are in [Service authoring](SERVICE_AUTHORING.md). New service code uses rmb_host, not module_ops/dispatch or ROM callback stubs.

Entry initializes normal drivers/tasks, registers listeners during INIT, and returns 0. Reject invalid ports/flags/duplicates and callbacks outside admitted module text. Gate callbacks until successful entry, and reset/recover after partial failure. Never hot reenter entry. Hello registers only3333 and contains no ROM stubs or raw socket imports. STM chooses3333 and UART3336 for legacy compatibility. Base retains logs3335 and management3337.

## 3. Implement transport ownership and authentication

- Reserve3335/3337; permit at most four listeners with bounded connection slots. Give each listener a worker; callbacks serialize per listener but may overlap across listeners. Do not put listener work on the management task.
- Authenticate the first bounded LF-delimited JSON object before callback. Require exactly one string token; reject duplicate token, malformed JSON, raw/decoded NUL, wrong credentials and header/token/time violations. Do not interpret business `op` or STM fields in base.
- AUTH_CONSUME (0u) discards the line; AUTH_REPLAY (1u) replays the exact original bytes first for legacy one-request JSON. No additional ACK. Optional `authenticated(candidate)` is defense-in-depth without sharing the token secret.
- Expose opaque borrowed int32_t rmb_connection, not raw fd. Base closes after callback; services cannot retain or close it. Use generation-bound slot IDs, checking generation and active state on every I/O.
- Implement positive partial counts, read EOF=0, RMB_IO_ERROR=-1 and RMB_IO_AGAIN=-2. Handle EAGAIN/EINTR correctly in nonblocking paths. Blocking read/write use limited adapter waits and may return partial data; they are not indefinite fill-buffer operations. Bool exact handles partial transfers against one absolute monotonic microsecond deadline; use the same clock as now_us and do not renew the deadline on each retry.

## 4. Supervise generic work, not STM commands

`run(rmb_job_fn,void *,int64_t timeout_us)` executes an admitted module function on the supervised worker, bounded to 1800 seconds. Context is borrowed until completion or whole-chip reset; a timeout must not leave live work holding a returned caller stack. Reject nested worker run or safely inline it; never queue-and-wait on the same worker. Long work calls progress. Keep WDT independent of management networking. Only SJ_INIT/SJ_CALL/SJ_HANG belong in base; STM composes legacy status itself.

## 5. Preserve update/reboot invariants

Invalidate the footer while running, then whole-chip restart into update-only mode. No service executes during module-body erase/write. Verify body format/exact binding/hash before footer commit; restart again before entering the module once. Boot-latched write permission is immutable. Do not hot-unload a mapped/executing service or execute uploaded code during verification/commit.

Footer invalidation is itself a flash operation, so do not claim absolutely no flash writes at normal boot. NVS may introduce ordinary IRQ delay. This work introduces no dedicated cache-off ISR subsystem; preserve normal ESP execution safety and document any separate hardware timing limits.

## 6. Kconfig, discovery and private builds

Prefer base keys RMB_ENABLE/WIFI_SSID/WIFI_PASSWORD/TOKEN/PORT/DISCOVERY_PORT with RMB_ prefixes, i.e. CONFIG_RMB_ENABLE, CONFIG_RMB_WIFI_SSID, CONFIG_RMB_WIFI_PASSWORD, CONFIG_RMB_TOKEN, CONFIG_RMB_PORT and CONFIG_RMB_DISCOVERY_PORT in C. The STM module retains its own ROM_SERVICE_DEVICE/pin configuration but no network credentials. Temporary CONFIG_ROM_SERVICE_* network aliases may ease safe compilation; verify implemented aliases rather than silently assuming them.

Discovery must advertise a generic bridge/module identity. Legacy STM discovery tags can be accepted as compatibility aliases without claiming hello is STM32. Keep secrets out of source, artifacts intended for publication, logs and examples. Public placeholder configurations are compile-only, not deployment configurations.

Use a local ESP-IDF 5.5.5 tree and its matching compiler/Python environment. Set explicit IDF_PATH/PATH and the appropriate local Python environment; do not rely on export.sh when optional GDB packages are absent. Build a module against the exact resulting ABI4 base ELF and actual reserved addresses. Synthetic module addresses only prove compilation/linking and are NOT DEPLOYABLE. Do not copy addresses or SHA from historical ABI3 reports.

## 7. Verification matrix and reporting

| Layer | Required evidence | Does not prove |
|---|---|---|
| Native core/API | actual test output for auth/replay, short I/O/deadlines, stale handles, INIT/ready gate, run lifetime/timeout/recovery | ESP scheduling, Wi-Fi or physical Flash |
| ESP cross-build | disabled and placeholder-enabled build output; exact ELF binding/import/section checks; hello A/B and STM builds | hardware deployment or target ROM behavior |
| Physical ABI4 (separately authorized) | Wi-Fi update/reboot, real WDT recovery, module storage faults, STM UART/SPI/pins and target behavior | never inferred from native tests or ABI3 history |

Record pass/fail/not-run and actual commands/environment in verification reports. Do not invent pass counts or claim hardware just because the linker or native mocks succeeded. Historical ABI3 A→B/restore and SPI observations remain unchanged evidence for that earlier version only; [Service authoring](SERVICE_AUTHORING.md) labels them explicitly. New ABI4 results are recorded separately. Budget the complete callback/SDK/libc stack: the configurable listener default is 12288 bytes, and actual low-water measurements are required for representative workloads.
