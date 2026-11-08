# Base/platform/service boundary — ABI 4

## Ownership

| Resident base / platform | Service implementation |
|---|---|
| Wi-Fi/network setup, admission authentication, bounded listeners/connection slots | Application protocol schema and responses |
| Generic module loading, exact ELF hash/address binding, data/BSS preparation | Normal module entry, drivers, tasks and listener registration |
| Module partition/storage, footer update, boot-latched permission | STM target firmware staging and target Flash/RAM geometry |
| Generic run supervision, progress/WDT, recovery and whole-chip reset | STM ROM algorithms, probe/download/restart and legacy STM status |
| Base logs3335, management3337, generic discovery | Chosen service ports: STM3333/UART3336; hello3333 only |
| Platform-specific storage/clock/scheduler/network adapters | STM UART/SPI, BOOT/NRST and GPIO/pin configuration |

The module-update slot and the STM target firmware staging area are different storage concerns. A resident module loader must not require a ROM device, target geometry, UART, staging pointer or target pins merely to run hello.

## Public ABI, not private platform imports

`include/rmb/service.h` is the stdint/stddef/stdbool-only public SDK, with `RMB_SERVICE_ABI=4u` and `MODULE_ABI_VERSION` as its alias. No ROM, IDF or socket types cross it. `module_entry_fn = int (*)(const rmb_host *)`: normal initialization, listener registration, then 0 on success. No module_ops, numbered dispatch or obligatory ROM_E_CAP stubs. Public services use rmb_host, even if a module_host migration typedef remains.

Ordered host fields: `abi,size,net,now_us,progress,run`; ordered net fields: `abi,size,listen,exact,read,write,authenticated`. See [the exact signatures](SERVICE_AUTHORING.md). Platform storage/lifecycle APIs are adapter contracts, not an excuse to expose platform socket ownership through service.h. Exact-ELF binding remains mandatory; a generic source API is not binary portability across base builds.

## Network boundary

`listen(uint16_t,uint32_t,rmb_connection_fn,void *)` returns 0 or negative failure. Up to four listeners use bounded base-managed live slots and separate listener workers: serial within a listener, concurrent across listeners. Management is not a business worker. Reserve3337/3335. Registration is INIT-only; reject invalid ports/flags/duplicates and callbacks outside admitted module text. Callbacks remain gated until entry succeeds; failed entry requires recovery/reset, not hot reentry.

Base authentication checks the first bounded LF JSON object with exactly one string token. Reject duplicate token, raw or decoded NUL, malformed JSON, bad credentials and header/token/time-limit violations. Business fields remain uninterpreted by the base. `RMB_NET_AUTH_CONSUME=0u` drops the line; `RMB_NET_AUTH_REPLAY=1u` returns its exact original bytes first, preserving old token/op-in-one-line protocols. Do not inject an ACK. Optional `authenticated(candidate)` lets legacy services check again without carrying the secret.

`rmb_connection` is an opaque borrowed int32_t handle, not fd. It expires when the callback returns; the base closes the OS connection. Services must neither close nor retain it. Generation-bound slot IDs and validation on every read/write prevent stale-slot session access. Partial positive transfers are normal; read 0 means EOF, error=-1, again=-2. Nonblocking paths handle EAGAIN/EINTR. Blocking calls have limited adapter waits; use bool `exact` for complete transfers by an absolute monotonic deadline, not a renewed per-chunk timeout.

## Execution and storage boundary

`run(rmb_job_fn,void *,int64_t timeout_us)` supervises a module-text function, with timeout <=1800s and progress for long work. Borrowed context survives until job completion or whole-chip reset; never timeout-return while a worker retains live stack pointers. Nested run safely inlines or rejects instead of deadlocking. Only SJ_INIT/SJ_CALL/SJ_HANG are base concepts. Service-specific STM status is built by STM, not exported as host state. WDT remains independent of management network activity.

Keep MEMPROT+LOCK, fixed module partition/footer and immutable per-boot write permission. Invalidate footer while running, restart whole chip, erase/write body only with no service executing, verify then commit, restart, and enter once. No hot unload, no callback activity during update-only body writes. Footer-only invalidation is itself flash I/O; NVS can cause ordinary IRQ delay. This refactor does not add a cache-off ISR subsystem or claim zero normal flash writes.

## Configuration and compatibility

Prefer resident Kconfig `RMB_ENABLE`, `RMB_WIFI_SSID`, `RMB_WIFI_PASSWORD`, `RMB_TOKEN`, `RMB_PORT`, `RMB_DISCOVERY_PORT` (C spellings gain `CONFIG_`). STM module owns its legacy `ROM_SERVICE_DEVICE` and pin settings, with no network credentials. Legacy `CONFIG_ROM_SERVICE_*` network names may be temporary compile-time compatibility aliases if required; they do not make STM behavior a base responsibility. Read actual Kconfig when selecting supported migration names; never add real credentials to public examples.

Discovery identity is generic. A legacy STM discovery tag may be accepted as a compatibility alias, not advertised as proof every module is STM32. Keep legacy STM JSON/client behavior on3333 and UART3336, with logs3335/management3337 owned by base.

## Evidence boundary

ABI4 build/native tests establish only their recorded compilation or simulated execution properties. Synthetic-address linkage is not deployable. Historical ABI3 Wi-Fi A→B, restore and SPI observations must remain labeled ABI3 and cannot establish ABI4 hardware behavior. Independent ABI4 results are now recorded in the [hardware report](validation/abi4-hardware-20261108.md); they are limited to the tested board/workloads. Existing historical reports are not rewritten as new results.
