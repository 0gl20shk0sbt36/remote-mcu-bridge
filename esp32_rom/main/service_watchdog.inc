/* Base-owned, explicit capability leases. No timer or base task feeds a role.
 * RTC is warm-reset retention only, never a power-loss or atomic-write promise.
 * Lifecycle is one-shot: begin INIT, finish on success, abort on any failure. */
#define SERVICE_LEASES 8
#define SERVICE_FAULT_MAGIC 0x57444c35u
#define SERVICE_UNKNOWN UINT32_MAX
static rmb_lock_t lease_lock = RMB_LOCK_INIT;
typedef struct {
    int id;
    bool root, active;
    uint32_t timeout_ms, checkpoint;
    int64_t last;
    char role[24];
} service_lease;
static service_lease leases[SERVICE_LEASES];
/* IDs are never reused within a boot, even when a temporary slot is freed. */
static int lease_next = 1;
static bool lease_enabled, lease_initializing, lease_attempted, lease_root_created;
typedef struct {
    uint32_t seq, reason, boot, reset, reset_cause, lease, checkpoint, timeout_ms, age_ms;
    uint32_t offender_unknown, last_feed_unknown;
    int64_t last_feed_us;
    char role[24];
} service_fault_record;
typedef struct {
    uint32_t magic, boot, current_reset, sequence;
    service_fault_record fault;
    /* Latest observed service progress is a breadcrumb, NOT proof that this role
     * caused a scheduler/IRQ stall. Hard-stall offender and expiry age are unknown. */
    service_fault_record breadcrumb;
    uint32_t crc;
} service_watchdog_journal;
static RMB_RETAINED service_watchdog_journal lease_rtc;
static uint32_t lease_boot;
/* CRC covers all retained bytes before crc to detect retention corruption;
 * it proves neither freshness/authenticity nor transactional RTC persistence. */
static uint32_t fault_crc(const service_watchdog_journal *r) {
    const uint8_t *p = (const uint8_t *)r;
    uint32_t c = ~0u;
    for (size_t i = 0; i < offsetof(service_watchdog_journal, crc); i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ ((0u - (c & 1u)) & 0xedb88320u);
    }
    return ~c;
}
static bool lease_record_valid(void) {
    return lease_rtc.magic == SERVICE_FAULT_MAGIC && lease_rtc.crc == fault_crc(&lease_rtc);
}
static void lease_record_commit(void) {
    lease_rtc.crc = fault_crc(&lease_rtc);
}
static uint32_t lease_new_sequence(void) {
    /* Never wrap and make an old ACK match a new event. At exhaustion the final
     * report is permanently sticky (ack rejects UINT32_MAX). */
    if (lease_rtc.sequence != UINT32_MAX)
        ++lease_rtc.sequence;
    return lease_rtc.sequence;
}
static bool lease_hardware_reset(rmb_reset_reason reason) {
    return reason == RMB_RST_TASK_WATCHDOG || reason == RMB_RST_INTERRUPT_WATCHDOG ||
           reason == RMB_RST_PANIC;
}
static void service_watchdog_boot(void) {
    bool valid = lease_record_valid();
    uint32_t reset = (uint32_t)rmb_port_reset_reason();
    if (!valid)
        memset(&lease_rtc, 0, sizeof lease_rtc);
    uint32_t previous_boot = lease_rtc.boot, previous_reset = lease_rtc.current_reset;
    lease_rtc.magic = SERVICE_FAULT_MAGIC;
    lease_boot = previous_boot == UINT32_MAX ? UINT32_MAX : previous_boot + 1;
    /* A new hardware fault must supersede an older pending report so a stale
     * ACK cannot erase this newer incident. Preserve a fault already captured
     * in the immediately preceding boot before its reset completed. */
    bool origin_captured = lease_rtc.fault.reason && lease_rtc.fault.boot == previous_boot &&
                           lease_rtc.fault.reset_cause == SERVICE_UNKNOWN;
    if (lease_hardware_reset(reset) && !origin_captured) {
        service_fault_record f = {0};
        f.seq = lease_new_sequence();
        f.reason = 2;
        f.boot = valid ? previous_boot : 0;
        f.reset = valid ? previous_reset : SERVICE_UNKNOWN;
        f.reset_cause = reset;
        /* Monotonic time restarts at boot; a breadcrumb cannot establish fault age
         * or identify the offender after a hardware stall. */
        f.age_ms = SERVICE_UNKNOWN;
        f.offender_unknown = 1;
        f.last_feed_unknown =
            !valid || lease_rtc.breadcrumb.last_feed_unknown || !lease_rtc.breadcrumb.lease;
        if (!f.last_feed_unknown) {
            f.last_feed_us = lease_rtc.breadcrumb.last_feed_us;
            f.checkpoint = lease_rtc.breadcrumb.checkpoint;
            f.timeout_ms = lease_rtc.breadcrumb.timeout_ms;
        }
        memcpy(f.role, "unknown", 8);
        lease_rtc.fault = f;
    } else if (lease_rtc.fault.reason && lease_rtc.fault.reset_cause == SERVICE_UNKNOWN) {
        /* Capture the reset ending the origin boot once, never overwrite on later
         * management reconnects/restarts. Origin boot's reset remains distinct. */
        lease_rtc.fault.reset_cause = reset;
    }
    lease_rtc.boot = lease_boot;
    lease_rtc.current_reset = reset;
    memset(&lease_rtc.breadcrumb, 0, sizeof lease_rtc.breadcrumb);
    lease_rtc.breadcrumb.last_feed_unknown = 1;
    lease_record_commit();
    memset(leases, 0, sizeof leases);
    lease_next = 1;
    lease_enabled = lease_initializing = lease_attempted = lease_root_created = false;
}
static bool service_watchdog_begin_init(void) {
    bool ok = false;
    rmb_port_lock_enter(&lease_lock);
    if (!lease_attempted && !lease_rtc.fault.reason) {
        lease_attempted = true;
        lease_enabled = true;
        lease_initializing = true;
        ok = true;
    }
    rmb_port_lock_exit(&lease_lock);
    return ok;
}
static void service_watchdog_abort_init(void) {
    rmb_port_lock_enter(&lease_lock);
    lease_enabled = false;
    lease_initializing = false;
    lease_attempted = true;
    for (unsigned i = 0; i < SERVICE_LEASES; i++)
        leases[i].active = false;
    /* Failed module tasks may still exist; their retained capabilities are dead. */
    memset(&lease_rtc.breadcrumb, 0, sizeof lease_rtc.breadcrumb);
    lease_rtc.breadcrumb.last_feed_unknown = 1;
    lease_record_commit();
    rmb_port_lock_exit(&lease_lock);
}
static void service_watchdog_finish_init(void) {
    rmb_port_lock_enter(&lease_lock);
    lease_initializing = false;
    rmb_port_lock_exit(&lease_lock);
}
/* Equality expires a lease; a late feed/close cannot hide a missed deadline.
 * A backwards clock is fail-closed here, unlike the supervisor job helper. */
static bool lease_overdue(const service_lease *l, int64_t now) {
    return now < l->last || now - l->last >= (int64_t)l->timeout_ms * 1000;
}
static int service_watchdog_open(const char *role, uint32_t timeout_ms) {
    if (rmb_port_in_isr() || !role || timeout_ms < 1000 || timeout_ms > 60000 ||
        strnlen(role, 24) >= 24 || !role[0])
        return -1;
    /* Restrict role bytes so diagnostic JSON never contains unescaped input. */
    for (const unsigned char *p = (const unsigned char *)role; *p; p++)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') ||
              *p == '_' || *p == '-' || *p == '.' || *p == ':'))
            return -1;
    int result = -1;
    rmb_port_lock_enter(&lease_lock);
    if (lease_enabled && (lease_initializing || lease_root_created) && lease_next < INT32_MAX)
        for (unsigned i = 0; i < SERVICE_LEASES; i++)
            if (!leases[i].active) {
                service_lease *l = &leases[i];
                /* First INIT lease is permanent root; later leases are independently due
                 * and may be closed only while live. Feeding root does not feed children. */
                *l = (service_lease){.id = lease_next++,
                                     .active = true,
                                     .root = !lease_root_created && lease_initializing,
                                     .timeout_ms = timeout_ms,
                                     .last = rmb_port_now_us()};
                memcpy(l->role, role, strlen(role) + 1);
                if (l->root)
                    lease_root_created = true;
                result = l->id;
                break;
            }
    rmb_port_lock_exit(&lease_lock);
    return result;
}
static int service_watchdog_feed(int id, uint32_t checkpoint) {
    if (rmb_port_in_isr())
        return -1;
    int result = -1;
    rmb_port_lock_enter(&lease_lock);
    if (lease_enabled)
        for (unsigned i = 0; i < SERVICE_LEASES; i++)
            if (leases[i].active && leases[i].id == id) {
                service_lease *l = &leases[i];
                int64_t now = rmb_port_now_us();
                if (!lease_overdue(l, now)) {
                    l->last = now;
                    l->checkpoint = checkpoint;
                    result = 0;
                    lease_rtc.breadcrumb = (service_fault_record){.lease = (uint32_t)id,
                                                                  .checkpoint = checkpoint,
                                                                  .timeout_ms = l->timeout_ms,
                                                                  .last_feed_us = now};
                    memcpy(lease_rtc.breadcrumb.role, l->role, sizeof l->role);
                    lease_record_commit();
                }
                break;
            }
    rmb_port_lock_exit(&lease_lock);
    return result;
}
static int service_watchdog_close(int id) {
    if (rmb_port_in_isr())
        return -1;
    int result = -1;
    rmb_port_lock_enter(&lease_lock);
    if (lease_enabled)
        for (unsigned i = 0; i < SERVICE_LEASES; i++)
            if (leases[i].active && leases[i].id == id && !leases[i].root &&
                !lease_overdue(&leases[i], rmb_port_now_us())) {
                leases[i].active = false;
                result = 0;
                break;
            }
    rmb_port_lock_exit(&lease_lock);
    return result;
}
bool module_watchdog_configured(void) {
    bool ok = false;
    rmb_port_lock_enter(&lease_lock);
    if (lease_enabled)
        for (unsigned i = 0; i < SERVICE_LEASES; i++)
            ok |=
                leases[i].active && leases[i].root && !lease_overdue(&leases[i], rmb_port_now_us());
    rmb_port_lock_exit(&lease_lock);
    return ok;
}
static void service_watchdog_poll(void) {
    bool expired = false;
    rmb_port_lock_enter(&lease_lock);
    int64_t now = rmb_port_now_us();
    if (lease_enabled)
        for (unsigned i = 0; i < SERVICE_LEASES; i++)
            if (leases[i].active && lease_overdue(&leases[i], now)) {
                service_lease *l = &leases[i];
                uint64_t age = now >= l->last ? (uint64_t)(now - l->last) / 1000 : UINT64_MAX;
                service_fault_record f = {.seq = lease_new_sequence(),
                                          .reason = 1,
                                          .boot = lease_boot,
                                          .reset = lease_rtc.current_reset,
                                          .reset_cause = SERVICE_UNKNOWN,
                                          .lease = (uint32_t)l->id,
                                          .checkpoint = l->checkpoint,
                                          .timeout_ms = l->timeout_ms,
                                          .last_feed_us = l->last,
                                          .age_ms = age >= UINT32_MAX ? UINT32_MAX : (uint32_t)age};
                memcpy(f.role, l->role, sizeof f.role);
                lease_rtc.fault = f;
                lease_record_commit();
                /* No task can revive/feed/close after the decision, even before restart. */
                lease_enabled = false;
                expired = true;
                break;
            }
    rmb_port_lock_exit(&lease_lock);
    if (expired)
        rmb_port_restart();
}
/* Generic entry/job faults share the exact same sticky sequence journal. */
static void service_supervisor_fault(uint32_t reason, bool entry, uint32_t checkpoint,
                                     int64_t began, int64_t progress, int64_t limit, int64_t now) {
    rmb_port_lock_enter(&lease_lock);
    /* Worker completion and monitor can observe the same incident. First capture
     * in this boot wins until reset; never create two ACK sequences for it. */
    if (!(lease_rtc.fault.reason && lease_rtc.fault.boot == lease_boot &&
          lease_rtc.fault.reset_cause == SERVICE_UNKNOWN)) {
        uint64_t elapsed =
            began >= 0 && now >= began ? (uint64_t)now - (uint64_t)began : UINT64_MAX;
        uint64_t budget = limit > 0 ? ((uint64_t)limit + 999) / 1000 : 0;
        service_fault_record f = {
            .seq = lease_new_sequence(),
            .reason = reason,
            .boot = lease_boot,
            .reset = lease_rtc.current_reset,
            .reset_cause = SERVICE_UNKNOWN,
            .checkpoint = checkpoint,
            .timeout_ms = budget >= UINT32_MAX ? UINT32_MAX : (uint32_t)budget,
            .age_ms = elapsed / 1000 >= UINT32_MAX ? UINT32_MAX : (uint32_t)(elapsed / 1000),
            .last_feed_unknown = !checkpoint || progress < 0,
            .last_feed_us = progress >= 0 ? progress : 0};
        const char *role = entry ? "module-entry" : "module-job";
        memcpy(f.role, role, strlen(role) + 1);
        lease_rtc.fault = f;
        lease_record_commit();
    }
    lease_enabled = false;
    rmb_port_lock_exit(&lease_lock);
}
static void service_fault_status(int fd) {
    char out[768];
    rmb_port_lock_enter(&lease_lock);
    service_fault_record r = lease_rtc.fault;
    uint32_t current_boot = lease_rtc.boot, current_reset = lease_rtc.current_reset;
    rmb_port_lock_exit(&lease_lock);
    snprintf(out, sizeof out,
             "{\"ok\":true,\"seq\":%lu,\"reason\":%lu,\"lease\":%lu,\"role\":\"%s\",\"checkpoint\":"
             "%lu,\"last_feed_us\":%lld,\"last_feed_unknown\":%s,\"timeout_ms\":%lu,\"age_ms\":%lu,"
             "\"age_unknown\":%s,\"offender_unknown\":%s,\"boot\":%lu,\"reset_reason\":%lu,\"fault_"
             "reset_reason\":%lu,\"current_boot\":%lu,\"current_reset_reason\":%lu}\n",
             (unsigned long)r.seq, (unsigned long)r.reason, (unsigned long)r.lease, r.role,
             (unsigned long)r.checkpoint, (long long)r.last_feed_us,
             r.last_feed_unknown ? "true" : "false", (unsigned long)r.timeout_ms,
             (unsigned long)r.age_ms, r.age_ms == UINT32_MAX ? "true" : "false",
             r.offender_unknown ? "true" : "false", (unsigned long)r.boot, (unsigned long)r.reset,
             (unsigned long)r.reset_cause, (unsigned long)current_boot,
             (unsigned long)current_reset);
    reply(fd, out);
}
static bool service_fault_pending(void) {
    bool pending;
    rmb_port_lock_enter(&lease_lock);
    pending = lease_record_valid() && lease_rtc.fault.reason != 0;
    rmb_port_lock_exit(&lease_lock);
    return pending;
}
/* ACK consumes one exact report sequence, not the supervisor quarantine latch. */
static bool service_fault_ack(uint32_t seq) {
    bool ok = false;
    rmb_port_lock_enter(&lease_lock);
    if (lease_record_valid() && lease_rtc.fault.reason && seq && seq != UINT32_MAX &&
        lease_rtc.fault.seq == seq) {
        memset(&lease_rtc.fault, 0, sizeof lease_rtc.fault);
        lease_record_commit();
        ok = true;
    }
    /* Deliberately does not enable leases or change quarantine/entry permission. */
    rmb_port_lock_exit(&lease_lock);
    return ok;
}
