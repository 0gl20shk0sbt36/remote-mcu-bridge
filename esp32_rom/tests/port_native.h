/* Native (host) implementation of the resident port contract for fixtures.
 *
 * Header-only and instrumented: fixtures include this instead of hand-written
 * SDK stubs, so they exercise the real port surface the resident compiles
 * against. No hardware, RTOS or ESP-IDF involved.
 */
#ifndef RMB_PORT_NATIVE_H
#define RMB_PORT_NATIVE_H

#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ── Types ─────────────────────────────────────────────────────────────── */
typedef pthread_mutex_t rmb_lock_t;
#define RMB_LOCK_INIT PTHREAD_MUTEX_INITIALIZER

typedef struct native_task *rmb_task_t;
typedef struct native_mutex *rmb_mutex_t;
typedef struct native_sem *rmb_sem_t;
typedef struct native_queue *rmb_queue_t;
typedef struct native_event *rmb_event_t;

#define RMB_CONFIG_TOKEN "native-public-token"
#define RMB_CONFIG_SSID "native-test-network"
#define RMB_CONFIG_PASSWORD "native-test-password"
#define RMB_CONFIG_PORT 3333
#define RMB_CONFIG_DISCOVERY_PORT 3334
#define RMB_CONFIG_LISTENER_STACK_BYTES 12288

#define RMB_RETAINED
#define RMB_DRAM_RESERVE_SECTION
#define RMB_WAIT_FOREVER UINT32_MAX
#define RMB_WAIT_NOW 0u
#define RMB_MS_TO_TICKS(ms) ((uint32_t)(ms))

typedef struct {
    uint32_t timeout_ms;
    uint32_t idle_core_mask;
    bool trigger_panic;
} rmb_wdt_config_t;

/* ── Instrumentation and hooks ─────────────────────────────────────────── */
static unsigned rmb_test_task_creates, rmb_test_task_deletes, rmb_test_notifies;
extern int rmb_test_task_fail_after; /* 0: next create fails; -1: never */
extern int rmb_test_resets;
static int rmb_test_wdt_registrations, rmb_test_wdt_feeds;
extern rmb_reset_reason rmb_test_reset_reason_value;
static void *rmb_test_current_task = (void *)1;
extern int64_t rmb_test_now_us_value; /* >= 0: fixed clock */
extern uint32_t rmb_test_stack_min_free;

struct native_task {
    int unused;
};
struct native_mutex {
    int locked;
};
struct native_sem {
    int count;
};
struct native_queue {
    unsigned length, item_bytes, count;
    unsigned char *storage;
};
struct native_event {
    uint32_t bits;
};

#endif /* RMB_PORT_NATIVE_H */
