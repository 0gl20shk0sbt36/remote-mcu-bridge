#ifndef RMB_SERVICE_H
#define RMB_SERVICE_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define RMB_SERVICE_ABI 4u
#define MODULE_ABI_VERSION RMB_SERVICE_ABI
#define RMB_NET_AUTH_CONSUME 0u
#define RMB_NET_AUTH_REPLAY 1u
/* Negative transfer results; AGAIN means retry without treating it as EOF. */
#define RMB_IO_ERROR (-1)
#define RMB_IO_AGAIN (-2)
/* Opaque generation-bound capability, not an OS socket descriptor. */
typedef int32_t rmb_connection;
typedef void (*rmb_connection_fn)(rmb_connection, void *);
typedef int (*rmb_job_fn)(void *);
typedef struct {
    uint32_t abi, size;
    /* Entry-task registration only; 0 succeeds, negative fails the INIT transaction.
     * Callbacks run serially per listener, concurrently across listeners. context
     * stays caller-owned and must outlive all callbacks (normally until reset).
     * CONSUME removes the auth line; REPLAY exposes its original bytes including LF. */
    int (*listen)(uint16_t, uint32_t, rmb_connection_fn, void *);
    /* writing selects direction; failure may already have transferred a prefix. */
    bool (*exact)(rmb_connection, void *, size_t, bool, int64_t);
    /* Final bool is nonblocking. Positive results are byte counts; read 0 is EOF
     * for nonempty requests. Zero-length transfers return 0 on a valid handle. */
    int (*read)(rmb_connection, void *, size_t, bool);
    int (*write)(rmb_connection, const void *, size_t, bool);
    /* Token comparison only: does not authenticate a new socket or own the string. */
    bool (*authenticated)(const char *);
} rmb_net_api;
typedef struct {
    uint32_t abi, size;
    const rmb_net_api *net;
    /* Monotonic microseconds since boot, not wall-clock time. */
    int64_t (*now_us)(void);
    /* Supervised-worker progress only; not a substitute for role lease feeds. */
    void (*progress)(void);
    /* Synchronous worker call; limit is a duration in microseconds, not a deadline. */
    int (*run)(rmb_job_fn, void *, int64_t);
    /* Open returns a nonnegative lease or negative error; timeout is milliseconds.
     * Feed/close return nonnegative success or negative error; never call in ISR. */
    int (*watchdog_open)(const char *role, uint32_t timeout_ms);
    int (*watchdog_feed)(int lease, uint32_t checkpoint);
    int (*watchdog_close)(int lease);
} rmb_host;
/* Borrowed handles expire on callback return. Blocking read/write <=5s;
 * exact uses absolute monotonic deadline. run retains context until completion
 * or reset; nesting rejected. Independent listeners need service serialization.
 * Watchdog leases: task-context only, root opened during entry persists to reset;
 * separate task-owned leases prevent healthy workers masking a stuck role. */
/* Host tables are resident-owned and remain valid until reset. Return 0 only
 * after successful initialization; nonzero aborts listener publication. */
typedef int (*module_entry_fn)(const rmb_host *);
#endif
