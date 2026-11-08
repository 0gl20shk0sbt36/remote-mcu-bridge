# Architecture — ABI 4

## Responsibility graph

```text
client
  ├─ management 3337 / base logs 3335 ───────────── resident base
  └─ service-selected listeners (3333, 3336, …)
       └─ base authentication + bounded connection slots
            └─ ready gate → module callback(rmb_connection, context)
                                 ├─ hello (3333 only)
                                 └─ STM service: ROM/device/staging/UART/SPI/pins

resident: network/auth → loader → generic supervisor/WDT/recovery
          storage/update/footer → exact ELF/hash/address admission
module:   module_entry(const rmb_host *) → drivers/tasks/listen → return 0
```

This is the ABI4 design. Independent [ABI4 hardware evidence](validation/abi4-hardware.md) is recorded separately from native/cross-build checks. Historical ABI3 physical results remain historical; see [Service authoring](SERVICE_AUTHORING.md).

## Public boundary

`include/rmb/service.h` is the public SDK. It includes only stdint.h/stddef.h/stdbool.h, never ROM, IDF or sockets. `RMB_SERVICE_ABI=4u`, with `MODULE_ABI_VERSION` an alias. `module_entry_fn` is `int (*)(const rmb_host *)`, not an ops-table factory. There are no dispatch numbers, required ROM stubs, or host device/staging pointers. A migration alias `module_host` is not the old ABI.

The ordered `rmb_host` fields are `uint32_t abi,size`, `const rmb_net_api *net`, `int64_t (*now_us)(void)`, `void (*progress)(void)`, and `int (*run)(rmb_job_fn,void *,int64_t timeout_us)`. `rmb_job_fn` is `int (*)(void *)`. The exact ordered network signatures are reproduced in [Service authoring](SERVICE_AUTHORING.md), the API reference for these guides.

The network table is `abi,size,listen,exact,read,write,authenticated`. Listener registration supplies a port, flags, module callback and context. It returns 0 or a negative failure; at most four listeners, one worker per listener, bounded live connection slots. Same-listener callbacks serialize; different listeners may run concurrently. Management 3337 and base logs 3335 are reserved. Neither STM nor hello imports raw socket APIs.

## Authentication and ownership

The base authenticates the first bounded LF-delimited JSON object before any service callback. It requires exactly one string token; rejects duplicate token, malformed JSON, raw/decoded NUL, bad credentials and length/time violations. Other fields are opaque business fields, not base-interpreted STM operations. AUTH_CONSUME (0u) discards the line; AUTH_REPLAY (1u) exposes its exact original bytes first for legacy one-request protocols. No extra ACK is injected. `authenticated(candidate)` supports optional legacy defense-in-depth without exposing the secret.

`rmb_connection` is an int32_t opaque borrowed handle, never an OS descriptor. Only the callback may use it, only during that callback; the base closes the connection after return. Generation-bound slot IDs and per-I/O active/generation validation prevent stale handles from reaching later sessions. Read/write allow partial transfers, positive counts, read EOF=0, RMB_IO_ERROR=-1 and RMB_IO_AGAIN=-2. Nonblocking I/O handles EAGAIN/EINTR. Blocking I/O has limited adapter waits, not an unlimited fill-buffer guarantee. `exact` handles partial transfers to a single absolute monotonic deadline and returns bool.

## Entry, jobs and recovery

Registration is INIT-only. Reject invalid ports/flags/duplicates/non-module callback addresses. Entry initializes ordinary service drivers/tasks and returns 0 only after successful setup. Admission/ready gating prevents callbacks until entry succeeds; failed or partial initialization requires recovery/reset, never hot reentry. Network work must not execute on the management task.

`run` validates a function inside the admitted image and executes it on the supervised worker. Its borrowed context remains valid until completion or whole-chip reset. Timeout cannot return while work retains live caller-stack pointers. Timeout is bounded to 1800 seconds; long jobs call progress. Nested run must safely inline or explicitly reject, never deadlock. The base tracks generic SJ_INIT/SJ_CALL/SJ_HANG only. STM constructs its own legacy status. Watchdog supervision stays independent of management networking.

## Storage/execution invariant

Retain exact ELF hash/address binding, the image header and 32-byte binding layout, fixed partition/footer geometry, MEMPROT+LOCK and immutable per-boot update permission. ABI3 binaries are intentionally rejected by ABI4.

```text
running service → footer invalidation → whole-chip restart
  → update-only boot → erase/write body (no service executes)
  → verify format/binding/hash → commit footer → whole-chip restart
  → admit and initialize entry once → ready
```

There is no hot unload. Footer invalidation itself is a flash operation; do not claim normal boot/service operation performs no flash writes at all. NVS may cause ordinary IRQ delays. ABI4 introduces no special cache-off ISR subsystem and does not prove malicious-module isolation.

## Service distinction

The STM service owns ROM algorithms, target geometry, firmware staging, BOOT/NRST, UART/SPI drivers and pins, plus legacy application responses. These are not resident prerequisites. It chooses legacy TCP3333 and UART3336 listeners as needed. The hello service chooses only3333, has no ROM stubs or target state, and uses the same authenticated generic network API. Base logs3335 and management3337 are not service-owned.

Discovery must identify a generic bridge/module host, not every service as STM32. A legacy discovery tag may remain an accepted compatibility alias; it does not imply the active module supports STM.
