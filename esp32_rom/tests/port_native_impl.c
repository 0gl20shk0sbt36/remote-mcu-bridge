/* Native implementation of the resident port contract (fixtures only). */
#include "rmb/port.h"

/* Instrumentation and hooks (declared extern in port_native.h). */
unsigned rmb_test_task_creates, rmb_test_task_deletes, rmb_test_notifies;
int rmb_test_task_fail_after = -1;
int rmb_test_resets;
int rmb_test_wdt_registrations, rmb_test_wdt_feeds;
rmb_reset_reason rmb_test_reset_reason_value = RMB_RST_POWER_ON;
void *rmb_test_current_task = (void *)1;
int64_t rmb_test_now_us_value = -1;
uint32_t rmb_test_stack_min_free = 4096;

/* ── Time ─────────────────────────────────────────────────────────────── */
int64_t rmb_port_now_us(void) {
    if (rmb_test_now_us_value >= 0)
        return rmb_test_now_us_value;
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
}

void rmb_port_delay_ticks(uint32_t ticks) {
    if (!ticks)
        return;
    struct timespec d = {ticks / 1000, (long)(ticks % 1000) * 1000000};
    nanosleep(&d, NULL);
}

/* ── Logging ──────────────────────────────────────────────────────────── */
void rmb_log_capture(const char *bytes, size_t length) {
    (void)bytes;
    (void)length;
}

void rmb_port_log(int level, const char *tag, const char *format, ...) {
    (void)level;
    (void)tag;
    (void)format;
}

#define RMB_LOGE(tag, ...) rmb_port_log(RMB_LOG_ERROR, tag, __VA_ARGS__)
#define RMB_LOGW(tag, ...) rmb_port_log(RMB_LOG_WARN, tag, __VA_ARGS__)
#define RMB_LOGI(tag, ...) rmb_port_log(RMB_LOG_INFO, tag, __VA_ARGS__)

/* ── Reset ────────────────────────────────────────────────────────────── */
void rmb_port_restart(void) {
    ++rmb_test_resets;
}

rmb_reset_reason rmb_port_reset_reason(void) {
    return rmb_test_reset_reason_value;
}

/* ── Locks ────────────────────────────────────────────────────────────── */
void rmb_port_lock_enter(rmb_lock_t *lock) {
    pthread_mutex_lock(lock);
}

void rmb_port_lock_exit(rmb_lock_t *lock) {
    pthread_mutex_unlock(lock);
}

/* ── Tasks ────────────────────────────────────────────────────────────── */
int rmb_port_task_create(const char *name, void (*function)(void *), void *context,
                                uint32_t stack_bytes, int priority, rmb_task_t *out) {
    (void)name;
    (void)function;
    (void)context;
    (void)stack_bytes;
    (void)priority;
    if (rmb_test_task_fail_after == 0)
        return -1;
    if (rmb_test_task_fail_after > 0)
        --rmb_test_task_fail_after;
    ++rmb_test_task_creates;
    if (out)
        *out = (rmb_task_t)(void *)2;
    return 0;
}

rmb_task_t rmb_port_task_current(void) {
    return (rmb_task_t)rmb_test_current_task;
}

void rmb_port_task_end(void) {
    ++rmb_test_task_deletes;
}

void rmb_port_task_terminate(rmb_task_t task) {
    (void)task;
    ++rmb_test_task_deletes;
}

void rmb_port_task_wait_notify(void) {}

void rmb_port_task_notify(rmb_task_t task) {
    (void)task;
    ++rmb_test_notifies;
}

uint32_t rmb_port_task_stack_min_free(void) {
    return rmb_test_stack_min_free;
}

bool rmb_port_in_isr(void) {
    return false;
}

/* ── Mutex / semaphore / queue / event ────────────────────────────────── */
int rmb_port_mutex_create(rmb_mutex_t *out) {
    *out = calloc(1, sizeof **out);
    return *out ? 0 : -1;
}

int rmb_port_mutex_take(rmb_mutex_t mutex, uint32_t ticks) {
    (void)ticks;
    if (mutex->locked)
        return -1;
    mutex->locked = 1;
    return 0;
}

void rmb_port_mutex_give(rmb_mutex_t mutex) {
    assert(mutex->locked);
    mutex->locked = 0;
}

void rmb_port_mutex_delete(rmb_mutex_t mutex) {
    free(mutex);
}

int rmb_port_sem_create(rmb_sem_t *out) {
    *out = calloc(1, sizeof **out);
    return *out ? 0 : -1;
}

int rmb_port_sem_take(rmb_sem_t semaphore, uint32_t ticks) {
    (void)ticks;
    if (!semaphore->count)
        return -1;
    semaphore->count = 0;
    return 0;
}

void rmb_port_sem_give(rmb_sem_t semaphore) {
    semaphore->count = 1;
}

void rmb_port_sem_delete(rmb_sem_t semaphore) {
    free(semaphore);
}

int rmb_port_queue_create(uint32_t length, uint32_t item_bytes, rmb_queue_t *out) {
    *out = calloc(1, sizeof **out);
    if (!*out)
        return -1;
    (*out)->length = length;
    (*out)->item_bytes = item_bytes;
    (*out)->storage = calloc(length ? length : 1, item_bytes ? item_bytes : 1);
    return (*out)->storage ? 0 : -1;
}

int rmb_port_queue_send(rmb_queue_t queue, const void *item, uint32_t ticks) {
    (void)ticks;
    if (queue->count >= queue->length)
        return -1;
    memcpy(queue->storage + queue->count * queue->item_bytes, item, queue->item_bytes);
    ++queue->count;
    return 0;
}

int rmb_port_queue_receive(rmb_queue_t queue, void *item, uint32_t ticks) {
    (void)ticks;
    if (!queue->count)
        return -1;
    memcpy(item, queue->storage, queue->item_bytes);
    --queue->count;
    memmove(queue->storage, queue->storage + queue->item_bytes, queue->count * queue->item_bytes);
    return 0;
}

int rmb_port_event_create(rmb_event_t *out) {
    *out = calloc(1, sizeof **out);
    return *out ? 0 : -1;
}

void rmb_port_event_set(rmb_event_t events, uint32_t bits) {
    events->bits |= bits;
}

void rmb_port_event_clear(rmb_event_t events, uint32_t bits) {
    events->bits &= ~bits;
}

uint32_t rmb_port_event_get(rmb_event_t events) {
    return events->bits;
}

uint32_t rmb_port_event_wait(rmb_event_t events, uint32_t bits, bool all, uint32_t ticks) {
    (void)ticks;
    if (all ? (events->bits & bits) != bits : !(events->bits & bits))
        return 0;
    return events->bits & bits;
}

/* ── Watchdog ─────────────────────────────────────────────────────────── */
int rmb_port_wdt_configure(const rmb_wdt_config_t *config) {
    (void)config;
    return 0;
}

int rmb_port_wdt_register_current(void) {
    ++rmb_test_wdt_registrations;
    return 0;
}

int rmb_port_wdt_feed(void) {
    ++rmb_test_wdt_feeds;
    return 0;
}

/* ── Storage and mapping are provided by each fixture ─────────────────── */
/* A fixture that exercises module_runtime must define these itself:
 *   rmb_port_retained, rmb_port_slot_* , rmb_port_base_sha256,
 *   rmb_port_net_start, rmb_port_net_info
 */
