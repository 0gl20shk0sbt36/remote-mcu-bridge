/* Native shims exercise the production include, not a watchdog reimplementation. */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include "port_native.h"
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include <stdlib.h>
#define RTC_NOINIT_ATTR
#define portMUX_INITIALIZER_UNLOCKED 0
typedef int portMUX_TYPE;
static int lock_depth, isr, reset_count, reset_reason = 1;
static int64_t clock_us;
static char response[1024];
#define ESP_RST_TASK_WDT 6
#define ESP_RST_INT_WDT 5
#define ESP_RST_WDT 7
#define ESP_RST_PANIC 4
static void enter(portMUX_TYPE *p) {
    (void)p;
    assert(lock_depth == 0);
    ++lock_depth;
}
static void leave(portMUX_TYPE *p) {
    (void)p;
    assert(lock_depth == 1);
    --lock_depth;
}
#define portENTER_CRITICAL(p) enter(p)
#define portEXIT_CRITICAL(p) leave(p)
static int xPortInIsrContext(void) {
    return isr;
}
static int64_t esp_timer_get_time(void) {
    return clock_us;
}
static int esp_reset_reason(void) {
    return reset_reason;
}
static void esp_restart(void) {
    assert(lock_depth == 0);
    ++reset_count;
}
static void reply(int fd, const char *s) {
    (void)fd;
    assert(lock_depth == 0);
    assert(strlen(s) < sizeof response);
    strcpy(response, s);
}
#include "../esp32_rom/main/service_watchdog.inc"
static void fresh(void) {
    memset(&lease_rtc, 0, sizeof lease_rtc);
    clock_us = 0;
    reset_reason = 1;
    reset_count = 0;
    isr = 0;
    service_watchdog_boot();
    assert(!service_fault_pending());
}
static int start(void) {
    assert(service_watchdog_begin_init());
    int root = service_watchdog_open("root", 60000);
    assert(root > 0);
    assert(module_watchdog_configured());
    service_watchdog_finish_init();
    return root;
}
static void test_gates_validation(void) {
    fresh();
    assert(service_watchdog_open("root", 1000) < 0);
    assert(service_watchdog_feed(1, 1) < 0);
    assert(service_watchdog_begin_init());
    assert(!service_watchdog_begin_init());
    assert(service_watchdog_open(NULL, 1000) < 0);
    assert(service_watchdog_open("", 1000) < 0);
    assert(service_watchdog_open("bad\"role", 1000) < 0);
    assert(service_watchdog_open("bad\\role", 1000) < 0);
    assert(service_watchdog_open("abcdefghijklmnopqrstuvwxyz", 1000) < 0);
    assert(service_watchdog_open("root", 999) < 0);
    assert(service_watchdog_open("root", 60001) < 0);
    isr = 1;
    assert(service_watchdog_open("root", 1000) < 0);
    assert(service_watchdog_feed(1, 1) < 0);
    assert(service_watchdog_close(1) < 0);
    isr = 0;
    int root = service_watchdog_open("root", 1000);
    assert(root > 0);
    assert(service_watchdog_close(root) < 0);
    assert(service_watchdog_feed(-1, 2) < 0);
    assert(service_watchdog_feed(0, 2) < 0);
    assert(service_watchdog_close(INT_MAX) < 0);
    service_watchdog_finish_init();
    assert(service_watchdog_open("control", 1000) > 0);
    puts("PASS task-only, lifecycle gates, role validation, invalid IDs, root close denied");
}
static void test_independent_and_boundary(void) {
    fresh();
    int root = start();
    int uart = service_watchdog_open("uart", 1000);
    assert(uart > 0);
    clock_us = 100;
    assert(service_watchdog_feed(uart, 42) == 0);
    clock_us = 1000099;
    assert(service_watchdog_feed(root, 99) == 0);
    service_watchdog_poll();
    assert(reset_count == 0);
    clock_us = 1000100;
    assert(service_watchdog_feed(uart, 43) < 0);
    assert(service_watchdog_close(uart) < 0);
    service_watchdog_poll();
    assert(reset_count == 1);
    assert(service_fault_pending());
    assert(lease_rtc.fault.lease == (uint32_t)uart);
    assert(!strcmp(lease_rtc.fault.role, "uart"));
    assert(lease_rtc.fault.checkpoint == 42);
    assert(lease_rtc.fault.last_feed_us == 100);
    assert(lease_rtc.fault.age_ms == 1000);
    assert(lease_rtc.fault.timeout_ms == 1000);
    assert(service_watchdog_feed(root, 100) < 0);
    service_watchdog_poll();
    assert(reset_count == 1);
    fresh();
    root = start();
    uart = service_watchdog_open("uart", 1000);
    clock_us = 999999;
    assert(service_watchdog_feed(uart, 1) == 0);
    clock_us = 1999998;
    assert(service_watchdog_feed(uart, 2) == 0); /* strictly before */
    clock_us = 2999998;
    assert(service_watchdog_feed(uart, 3) < 0); /* exact deadline */
    clock_us = 3000000;
    assert(service_watchdog_feed(uart, 4) < 0);
    service_watchdog_poll();
    assert(reset_count == 1);
    fresh();
    assert(service_watchdog_begin_init());
    root = service_watchdog_open("root", 1000);
    service_watchdog_finish_init();
    clock_us = 1000000;
    assert(!module_watchdog_configured());
    assert(service_watchdog_feed(root, 1) < 0);
    service_watchdog_poll();
    assert(reset_count == 1);
    puts("PASS independent role expiry, last-feed checkpoint, deadline -1/exact/+1, no revival");
}
static void test_separate_callbacks(void) {
    for (unsigned offender = 0; offender < 2; offender++) {
        fresh();
        int root = start();
        int roles[2] = {service_watchdog_open("control", 1000),
                        service_watchdog_open("uart", 1000)};
        assert(roles[0] > 0 && roles[1] > 0);
        clock_us = 900000;
        assert(service_watchdog_feed(roles[1 - offender], 55) == 0);
        assert(service_watchdog_feed(root, 66) == 0);
        clock_us = 1000000;
        service_watchdog_poll();
        assert(reset_count == 1);
        assert(lease_rtc.fault.lease == (uint32_t)roles[offender]);
    }
    puts("PASS independent control/UART callbacks each expire while other role and root feed");
}
static void test_capacity_stale(void) {
    fresh();
    start();
    int ids[SERVICE_LEASES - 1];
    for (unsigned i = 0; i < SERVICE_LEASES - 1; i++) {
        ids[i] = service_watchdog_open("worker", 1000);
        assert(ids[i] > 0);
    }
    assert(service_watchdog_open("overflow", 1000) < 0);
    int old = ids[0];
    assert(service_watchdog_close(old) == 0);
    int newer = service_watchdog_open("replacement", 1000);
    assert(newer > old);
    assert(service_watchdog_feed(old, 7) < 0);
    assert(service_watchdog_close(old) < 0);
    assert(service_watchdog_feed(newer, 8) == 0);
    for (unsigned i = 0; i < 10000; i++) {
        int stale = newer;
        assert(service_watchdog_close(newer) == 0);
        newer = service_watchdog_open("reused", 1000);
        assert(newer > stale);
        assert(service_watchdog_feed(stale, 9) < 0);
    }
    lease_next = INT32_MAX;
    assert(service_watchdog_close(newer) == 0);
    assert(service_watchdog_open("exhausted", 1000) < 0);
    puts("PASS bounded capacity, 10000 slot reuses, stale nonalias, ID exhaustion");
}
static void test_journal_ack_retention(void) {
    fresh();
    start();
    int uart = service_watchdog_open("uart", 1000);
    clock_us = 11;
    assert(service_watchdog_feed(uart, 123) == 0);
    clock_us = 1000011;
    service_watchdog_poll();
    service_fault_record origin = lease_rtc.fault;
    assert(origin.seq == 1);
    assert(!service_fault_ack(0));
    assert(!service_fault_ack(2));
    service_watchdog_journal before = lease_rtc;
    service_fault_status(123);
    assert(strstr(response, "\"role\":\"uart\""));
    assert(strstr(response, "\"seq\":1"));
    assert(memcmp(&before, &lease_rtc, sizeof before) == 0);
    clock_us = 0;
    reset_reason = 3;
    service_watchdog_boot();
    assert(service_fault_pending());
    assert(lease_rtc.fault.boot == origin.boot);
    assert(lease_rtc.fault.reset == origin.reset);
    assert(lease_rtc.fault.reset_cause == 3);
    assert(lease_rtc.boot == 2);
    assert(lease_rtc.current_reset == 3);
    assert(!service_watchdog_begin_init());
    reset_reason = 3;
    service_watchdog_boot();
    assert(lease_rtc.fault.seq == 1);
    assert(lease_rtc.fault.reset_cause == 3);
    assert(lease_rtc.fault.checkpoint == 123);
    assert(lease_rtc.fault.last_feed_us == 11);
    assert(service_fault_ack(origin.seq));
    assert(!service_fault_pending());
    assert(!service_fault_ack(origin.seq));
    /* Ack does not enable leases. A separately authorized new boot may start. */
    assert(service_watchdog_open("root", 1000) < 0);
    reset_reason = 3;
    service_watchdog_boot();
    start();
    uart = service_watchdog_open("control", 1000);
    clock_us = 1000000;
    service_watchdog_poll();
    assert(lease_rtc.fault.seq == 2);
    assert(!service_fault_ack(origin.seq));
    assert(service_fault_pending());
    assert(service_fault_ack(2));
    puts("PASS sticky read, exact-seq ACK, stale ACK rejects newer, retained origin/reset "
         "provenance");
}
static void test_hardware_crc(void) {
    fresh();
    int root = start();
    clock_us = 333;
    assert(service_watchdog_feed(root, 88) == 0);
    reset_reason = ESP_RST_INT_WDT;
    clock_us = 0;
    service_watchdog_boot();
    assert(service_fault_pending());
    assert(lease_rtc.fault.reason == 2);
    assert(lease_rtc.fault.boot == 1);
    assert(lease_rtc.fault.age_ms == UINT32_MAX);
    assert(lease_rtc.fault.offender_unknown);
    assert(!strcmp(lease_rtc.fault.role, "unknown"));
    assert(lease_rtc.fault.last_feed_us == 333);
    assert(lease_rtc.fault.checkpoint == 88);
    assert(!lease_rtc.fault.last_feed_unknown);
    assert(lease_rtc.fault.reset == 1);
    assert(lease_rtc.fault.reset_cause == ESP_RST_INT_WDT);
    lease_rtc.fault.role[0] ^= 1;
    assert(!lease_record_valid());
    reset_reason = 3;
    service_watchdog_boot();
    assert(lease_record_valid());
    assert(!service_fault_pending());
    assert(lease_rtc.boot == 1);
    memset(&lease_rtc, 0, sizeof lease_rtc);
    reset_reason = ESP_RST_WDT;
    service_watchdog_boot();
    assert(service_fault_pending());
    assert(lease_rtc.fault.last_feed_unknown);
    assert(lease_rtc.fault.boot == 0);
    assert(lease_rtc.fault.reset == SERVICE_UNKNOWN);
    assert(lease_rtc.fault.age_ms == SERVICE_UNKNOWN);
    puts("PASS simulated reset retention, hardware-stall unknown attribution/age, CRC corruption "
         "rejection");
}
static void test_abort_and_quarantine(void) {
    fresh();
    assert(service_watchdog_begin_init());
    int root = service_watchdog_open("root", 1000);
    int child = service_watchdog_open("child", 1000);
    assert(root > 0 && child > 0);
    service_watchdog_abort_init();
    assert(!module_watchdog_configured());
    assert(!service_watchdog_begin_init());
    assert(service_watchdog_feed(root, 1) < 0);
    assert(service_watchdog_feed(child, 1) < 0);
    assert(service_watchdog_open("resurrect", 1000) < 0);
    for (unsigned i = 0; i < 100; i++) {
        clock_us += 1000000;
        service_watchdog_poll();
    }
    assert(reset_count == 0);
    fresh(); /* update-only boot never begins INIT */
    clock_us = 1000000000;
    service_watchdog_poll();
    assert(reset_count == 0);
    assert(!module_watchdog_configured());
    reset_reason = ESP_RST_TASK_WDT;
    service_watchdog_boot();
    assert(service_fault_pending());
    assert(!service_watchdog_begin_init());
    assert(service_watchdog_open("root", 1000) < 0);
    clock_us += 1000000000;
    service_watchdog_poll();
    assert(reset_count == 0);
    puts(
        "PASS failed-init abort disables all leases, no reset loop, update/quarantined boot gates");
}
static void test_new_hardware_fault(void) {
    fresh();
    start();
    int control = service_watchdog_open("control", 1000);
    clock_us = 1000000;
    service_watchdog_poll();
    uint32_t old = lease_rtc.fault.seq;
    assert(old == 1);
    reset_reason = 3;
    clock_us = 0;
    service_watchdog_boot();
    assert(service_fault_pending());
    assert(lease_rtc.fault.seq == old);
    /* Base hardware failure in a later quarantined boot is a NEW incident. */
    reset_reason = ESP_RST_INT_WDT;
    service_watchdog_boot();
    assert(lease_rtc.fault.seq == 2);
    assert(lease_rtc.fault.reason == 2);
    assert(lease_rtc.fault.offender_unknown && lease_rtc.fault.last_feed_unknown);
    assert(!service_fault_ack(old));
    assert(service_fault_pending());
    assert(service_fault_ack(2));
    /* A hardware reset completing the already recorded timeout is not counted twice. */
    fresh();
    start();
    control = service_watchdog_open("control", 1000);
    assert(control > 0);
    clock_us = 1000000;
    service_watchdog_poll();
    old = lease_rtc.fault.seq;
    reset_reason = ESP_RST_TASK_WDT;
    clock_us = 0;
    service_watchdog_boot();
    assert(lease_rtc.fault.seq == old);
    assert(lease_rtc.fault.reason == 1);
    assert(lease_rtc.fault.reset_cause == ESP_RST_TASK_WDT);
    puts("PASS new hardware incident supersedes pending report; same-origin reset is not "
         "double-counted");
}
static void test_supervisor_fault_journal(void) {
    fresh();
    start();
    service_supervisor_fault(3, false, 17, 100, 600, 1000, 1200);
    assert(service_fault_pending());
    assert(lease_rtc.fault.reason == 3 && lease_rtc.fault.seq == 1);
    assert(!strcmp(lease_rtc.fault.role, "module-job"));
    assert(lease_rtc.fault.checkpoint == 17);
    assert(lease_rtc.fault.last_feed_us == 600 && !lease_rtc.fault.last_feed_unknown);
    assert(lease_rtc.fault.age_ms == 1 && lease_rtc.fault.timeout_ms == 1);
    service_supervisor_fault(3, false, 18, 100, 700, 1000, 1400);
    assert(lease_rtc.fault.seq == 1 && lease_rtc.fault.checkpoint == 17);
    reset_reason = 3;
    service_watchdog_boot();
    assert(service_fault_pending());
    assert(lease_rtc.fault.reset_cause == 3);
    assert(service_fault_ack(1));
    fresh();
    start();
    service_supervisor_fault(4, true, 0, 10, 10, 60000000, 11);
    service_watchdog_abort_init();
    assert(service_fault_pending());
    assert(lease_rtc.fault.reason == 4 && !strcmp(lease_rtc.fault.role, "module-entry"));
    assert(lease_rtc.fault.last_feed_unknown);
    assert(!module_watchdog_configured());
    puts("PASS generic deadline/init-failure sequence journal, same-incident deduplication, "
         "soft-reset retention");
}
int main(void) {
    test_gates_validation();
    test_independent_and_boundary();
    test_separate_callbacks();
    test_capacity_stale();
    test_journal_ack_retention();
    test_hardware_crc();
    test_abort_and_quarantine();
    test_new_hardware_fault();
    test_supervisor_fault_journal();
    assert(lock_depth == 0);
    puts("ALL production watchdog tests passed");
    return 0;
}
