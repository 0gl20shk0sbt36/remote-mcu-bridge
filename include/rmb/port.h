/* Port contract for the resident base (ABI4).
 *
 * The resident's logic — authentication, management protocol, log ring,
 * supervision, single-slot update, module admission — is portable C that talks
 * only to this header. A new host platform implements these functions and
 * nothing else: no resident file should need platform headers, and no platform
 * should need resident internals.
 *
 * The platform header (selected with -DRMB_PORT_PLATFORM_HEADER="...") supplies
 * the concrete types and attributes:
 *
 *   rmb_lock_t          critical-section lock storage
 *   RMB_LOCK_INIT       static initializer for it
 *   rmb_task_t          task handle
 *   rmb_mutex_t         mutex handle
 *   rmb_sem_t           binary semaphore handle
 *   rmb_queue_t         queue handle
 *   rmb_event_t         event-group handle
 *   RMB_RETAINED        attribute for RAM that survives a software reset
 *   RMB_WAIT_FOREVER / RMB_WAIT_NOW / RMB_MS_TO_TICKS(ms)
 *   rmb_wdt_config_t    watchdog configuration struct
 *
 * Requirements, not conveniences:
 *  - now_us is monotonic and never goes backwards within a boot.
 *  - retained memory holds its contents across a software reset but is not
 *    promised to survive power loss or a hard brownout.
 *  - slot_* address the single business slot as byte offsets from the slot base.
 *  - slot_map must place executable code where modue text is linked and keep the
 *    data region writable; slot_lock must make both immutable if the platform
 *    can enforce it, and must fail closed if it cannot.
 *  - net_start returns once the interface is usable (address acquired) or fails;
 *    it may be asynchronous only if net_info reports readiness.
 *  - Every function is callable from task context. Only lock_*, log (already
 *    guarded by the resident's own lock) and wdt_feed are ever used from
 *    interrupt context in this code base.
 */
#ifndef RMB_PORT_H
#define RMB_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef RMB_PORT_PLATFORM_HEADER
#error "define RMB_PORT_PLATFORM_HEADER, e.g. -DRMB_PORT_PLATFORM_HEADER=\"platform/esp_idf_port.h\""
#endif
#include RMB_PORT_PLATFORM_HEADER

/* Build-time configuration supplied by the platform header. A port maps these
 * to whatever its build system exposes; the resident never reads a platform
 * configuration API directly. */
#ifndef RMB_CONFIG_TOKEN
#error "platform header must define RMB_CONFIG_TOKEN (16..128 bytes)"
#endif
#ifndef RMB_CONFIG_SSID
#error "platform header must define RMB_CONFIG_SSID"
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

/* The platform network layer reports link/address changes to the resident. */
void rmb_resident_net_event(int connected);

/* Implemented by the resident: the platform logger offers every formatted line
 * for bounded capture. Must not allocate, log, do I/O or wait. */
void rmb_log_capture(const char *bytes, size_t length);

/* Log levels mirror the usual embedded scale; a platform may drop levels. */
enum { RMB_LOG_ERROR = 0, RMB_LOG_WARN = 1, RMB_LOG_INFO = 2, RMB_LOG_DEBUG = 3 };
void rmb_port_log(int level, const char *tag, const char *format, ...)
    __attribute__((format(printf, 3, 4)));
#define RMB_LOGE(tag, ...) rmb_port_log(RMB_LOG_ERROR, tag, __VA_ARGS__)
#define RMB_LOGW(tag, ...) rmb_port_log(RMB_LOG_WARN, tag, __VA_ARGS__)
#define RMB_LOGI(tag, ...) rmb_port_log(RMB_LOG_INFO, tag, __VA_ARGS__)

/* Time: monotonic microseconds, plus a bounded sleep. */
int64_t rmb_port_now_us(void);
void rmb_port_delay_ticks(uint32_t ticks);

/* Whole-chip reset and its provenance. */
/* Numeric values are the established ESP-IDF reset-reason codes, because they
 * are persisted in the fault journal and reported to clients by value. A port
 * maps its own reset causes onto these; do not renumber. */
typedef enum {
    RMB_RST_UNKNOWN = 0,
    RMB_RST_POWER_ON = 1,
    RMB_RST_SOFTWARE = 3,
    RMB_RST_PANIC = 4,
    RMB_RST_INTERRUPT_WATCHDOG = 5,
    RMB_RST_TASK_WATCHDOG = 6,
    RMB_RST_OTHER = 7
} rmb_reset_reason;
void rmb_port_restart(void);
rmb_reset_reason rmb_port_reset_reason(void);

/* Critical section against task/interrupt preemption. */
void rmb_port_lock_enter(rmb_lock_t *lock);
void rmb_port_lock_exit(rmb_lock_t *lock);

/* Tasks. stack_bytes is a byte count. task_create returns 0 or a negative code. */
int rmb_port_task_create(const char *name, void (*function)(void *), void *context,
                         uint32_t stack_bytes, int priority, rmb_task_t *out);
rmb_task_t rmb_port_task_current(void);
void rmb_port_task_end(void);
/* Terminate another task by handle (initialization rollback only). */
void rmb_port_task_terminate(rmb_task_t task);
void rmb_port_task_wait_notify(void);
void rmb_port_task_notify(rmb_task_t task);
uint32_t rmb_port_task_stack_min_free(void);
/* True when called from interrupt context; the resident refuses blocking work there. */
bool rmb_port_in_isr(void);

/* Mutex / binary semaphore / queue / event group. */
int rmb_port_mutex_create(rmb_mutex_t *out);
int rmb_port_mutex_take(rmb_mutex_t mutex, uint32_t ticks);
void rmb_port_mutex_give(rmb_mutex_t mutex);
void rmb_port_mutex_delete(rmb_mutex_t mutex);

int rmb_port_sem_create(rmb_sem_t *out);
int rmb_port_sem_take(rmb_sem_t semaphore, uint32_t ticks);
void rmb_port_sem_give(rmb_sem_t semaphore);
void rmb_port_sem_delete(rmb_sem_t semaphore);

int rmb_port_queue_create(uint32_t length, uint32_t item_bytes, rmb_queue_t *out);
int rmb_port_queue_send(rmb_queue_t queue, const void *item, uint32_t ticks);
int rmb_port_queue_receive(rmb_queue_t queue, void *item, uint32_t ticks);

int rmb_port_event_create(rmb_event_t *out);
void rmb_port_event_set(rmb_event_t events, uint32_t bits);
void rmb_port_event_clear(rmb_event_t events, uint32_t bits);
uint32_t rmb_port_event_get(rmb_event_t events);
uint32_t rmb_port_event_wait(rmb_event_t events, uint32_t bits, bool all, uint32_t ticks);

/* Watchdog: the platform owns the hardware, the resident owns the policy. */
int rmb_port_wdt_configure(const rmb_wdt_config_t *config);
int rmb_port_wdt_register_current(void);
int rmb_port_wdt_feed(void);

/* Persistent fault journal storage: RAM retained across a software reset. */
void *rmb_port_retained(size_t bytes, size_t align);

/* Business-slot storage. Offsets are relative to the slot base. */
int rmb_port_slot_geometry(uint32_t *size, uint32_t *footer_size);
int rmb_port_slot_read(uint32_t offset, void *destination, uint32_t bytes);
int rmb_port_slot_write(uint32_t offset, const void *source, uint32_t bytes);
int rmb_port_slot_erase(uint32_t offset, uint32_t bytes);
int rmb_port_slot_sha256(uint32_t offset, uint32_t bytes, uint8_t digest[32]);
/* Map the slot for execution and report the executable base. Text is linked at
 * base + module header; the writable data window is the resident's own array. */
int rmb_port_slot_map(uint32_t *text_address);
int rmb_port_slot_unmap(void);
/* Make the mapped text/data immutable. Returns 0 only if the platform enforced it. */
int rmb_port_slot_lock(void);

/* Identity of the running base image, for exact service binding. */
int rmb_port_base_sha256(uint8_t digest[32]);

/* Network bring-up. The platform owns association/DHCP details; the resident
 * only needs to know when an address exists and what it is. */
typedef struct {
    uint32_t address;  /* network byte order */
    uint32_t netmask;  /* network byte order */
    uint32_t gateway;  /* network byte order */
    uint8_t mac[6];
} rmb_net_info;
int rmb_port_net_start(const char *ssid, const char *password);
/* Non-zero return means an address is currently assigned. */
int rmb_port_net_info(rmb_net_info *info);

#endif /* RMB_PORT_H */
