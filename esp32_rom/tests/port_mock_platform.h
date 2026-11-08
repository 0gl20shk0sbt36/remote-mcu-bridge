/* Platform header for extraction fixtures that mock FreeRTOS/ESP APIs.
 *
 * These fixtures keep their own FreeRTOS-style mocks (int/pointer handles,
 * pdTRUE semantics). This header gives the port contract types that accept
 * those mocks, so production code can call rmb_port_* while the fixture still
 * owns the implementation. No pthread or struct-handle assumptions.
 */
#ifndef RMB_PLATFORM_MOCK_PORT_H
#define RMB_PLATFORM_MOCK_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <pthread.h>

/* Fixtures already implement these macros for their mock lock objects. */
typedef pthread_mutex_t rmb_lock_t;
#define RMB_LOCK_INIT PTHREAD_MUTEX_INITIALIZER

typedef void *rmb_task_t;
typedef void *rmb_mutex_t;
typedef void *rmb_sem_t;
typedef void *rmb_queue_t;
typedef void *rmb_event_t;

#define RMB_RETAINED
#define RMB_DRAM_RESERVE_SECTION

#define RMB_WAIT_FOREVER 0xffffffffu
#define RMB_WAIT_NOW 0u
#define RMB_MS_TO_TICKS(ms) ((uint32_t)(ms))

typedef struct {
    uint32_t timeout_ms;
    uint32_t idle_core_mask;
    bool trigger_panic;
} rmb_wdt_config_t;

/* Fixtures define CONFIG_RMB_TOKEN (or RMB_CONFIG_TOKEN) themselves. */
#ifndef RMB_CONFIG_TOKEN
#ifdef CONFIG_RMB_TOKEN
#define RMB_CONFIG_TOKEN CONFIG_RMB_TOKEN
#else
#define RMB_CONFIG_TOKEN "native-public-token"
#endif
#endif
#ifndef RMB_CONFIG_SSID
#define RMB_CONFIG_SSID "native-test-network"
#endif
#ifndef RMB_CONFIG_PASSWORD
#define RMB_CONFIG_PASSWORD ""
#endif
#ifndef RMB_CONFIG_PORT
#define RMB_CONFIG_PORT 3333
#endif
#ifndef RMB_CONFIG_DISCOVERY_PORT
#define RMB_CONFIG_DISCOVERY_PORT 3334
#endif
#ifndef RMB_CONFIG_LISTENER_STACK_BYTES
#define RMB_CONFIG_LISTENER_STACK_BYTES 12288
#endif

#endif /* RMB_PLATFORM_MOCK_PORT_H */
