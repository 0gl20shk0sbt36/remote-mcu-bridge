/* ESP-IDF implementation of the resident port contract (rmb/port.h).
 *
 * Everything platform-specific the resident needs lives here or in
 * net_esp_idf.c. A port for another MCU/RTOS replaces this file pair and keeps
 * the resident unchanged.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "esp_app_desc.h"
#include "esp_err.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"
#include "sdkconfig.h"

#include "rmb/port.h"

#define BUSINESS_LABEL "business"
#define BUSINESS_TYPE ESP_PARTITION_TYPE_DATA
#define BUSINESS_SUBTYPE 0x41
/* Must match the resident's fixed single-slot geometry contract. */
#define SLOT_SIZE 0x100000u
#define SLOT_ADDRESS 0x220000u
#define SLOT_FOOTER 4096u

static esp_partition_mmap_handle_t mapped_handle;
static const void *mapped_base;
static uintptr_t reserved_base;

/* Per-function semantics (ranges, ownership, failure modes) are documented once
 * in include/rmb/port.h; these bodies are deliberately thin mappings. */

/* ── Time ─────────────────────────────────────────────────────────────── */
int64_t rmb_port_now_us(void) {
    return esp_timer_get_time();
}

void rmb_port_delay_ticks(uint32_t ticks) {
    vTaskDelay(ticks);
}

/* ── Logging: console plus the resident's bounded capture hook ─────────── */
void rmb_port_log(int level, const char *tag, const char *format, ...) {
    char buffer[256];
    static const char marks[] = {'E', 'W', 'I', 'D'};
    va_list args;
    va_start(args, format);
    int n = vsnprintf(buffer, sizeof buffer, format, args);
    va_end(args);
    if (n <= 0)
        return;
    size_t used = (size_t)n;
    if (used >= sizeof buffer) {
        static const char mark[] = " [log truncated]\n";
        used = sizeof buffer - 1;
        memcpy(buffer + used - (sizeof(mark) - 1), mark, sizeof(mark) - 1);
    }
    printf("%c (%lld) %s: %s\n",
           marks[level < 0   ? 0
                 : level > 3 ? 3
                             : level],
           (long long)(esp_timer_get_time() / 1000), tag ? tag : "rmb", buffer);
    rmb_log_capture(buffer, used);
}

/* ── Reset ────────────────────────────────────────────────────────────── */
void rmb_port_restart(void) {
    esp_restart();
}

rmb_reset_reason rmb_port_reset_reason(void) {
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON:
        return RMB_RST_POWER_ON;
    case ESP_RST_SW:
        return RMB_RST_SOFTWARE;
    case ESP_RST_TASK_WDT:
        return RMB_RST_TASK_WATCHDOG;
    case ESP_RST_INT_WDT:
        return RMB_RST_INTERRUPT_WATCHDOG;
    case ESP_RST_PANIC:
        return RMB_RST_PANIC;
    case ESP_RST_UNKNOWN:
        return RMB_RST_UNKNOWN;
    default:
        return RMB_RST_OTHER;
    }
}

/* ── Critical sections ────────────────────────────────────────────────── */
void rmb_port_lock_enter(rmb_lock_t *lock) {
    portENTER_CRITICAL(lock);
}

void rmb_port_lock_exit(rmb_lock_t *lock) {
    portEXIT_CRITICAL(lock);
}

/* ── Tasks ────────────────────────────────────────────────────────────── */
int rmb_port_task_create(const char *name, void (*function)(void *), void *context,
                         uint32_t stack_bytes, int priority, rmb_task_t *out) {
    rmb_task_t handle = NULL;
    if (xTaskCreate(function, name, stack_bytes, context, (UBaseType_t)priority, &handle) != pdPASS)
        return -1;
    if (out)
        *out = handle;
    return 0;
}

rmb_task_t rmb_port_task_current(void) {
    return xTaskGetCurrentTaskHandle();
}

void rmb_port_task_end(void) {
    vTaskDelete(NULL);
}

void rmb_port_task_terminate(rmb_task_t task) {
    if (task)
        vTaskDelete(task);
}

void rmb_port_task_wait_notify(void) {
    (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
}

void rmb_port_task_notify(rmb_task_t task) {
    xTaskNotifyGive(task);
}

uint32_t rmb_port_task_stack_min_free(void) {
    return (uint32_t)uxTaskGetStackHighWaterMark(NULL);
}

bool rmb_port_in_isr(void) {
    return xPortInIsrContext() != 0;
}

/* ── Mutex / semaphore / queue / event group ───────────────────────────── */
int rmb_port_mutex_create(rmb_mutex_t *out) {
    *out = xSemaphoreCreateMutex();
    return *out ? 0 : -1;
}

int rmb_port_mutex_take(rmb_mutex_t mutex, uint32_t ticks) {
    return xSemaphoreTake(mutex, ticks) == pdTRUE ? 0 : -1;
}

void rmb_port_mutex_give(rmb_mutex_t mutex) {
    xSemaphoreGive(mutex);
}

void rmb_port_mutex_delete(rmb_mutex_t mutex) {
    vSemaphoreDelete(mutex);
}

int rmb_port_sem_create(rmb_sem_t *out) {
    *out = xSemaphoreCreateBinary();
    return *out ? 0 : -1;
}

int rmb_port_sem_take(rmb_sem_t semaphore, uint32_t ticks) {
    return xSemaphoreTake(semaphore, ticks) == pdTRUE ? 0 : -1;
}

void rmb_port_sem_give(rmb_sem_t semaphore) {
    xSemaphoreGive(semaphore);
}

void rmb_port_sem_delete(rmb_sem_t semaphore) {
    vSemaphoreDelete(semaphore);
}

int rmb_port_queue_create(uint32_t length, uint32_t item_bytes, rmb_queue_t *out) {
    *out = xQueueCreate((UBaseType_t)length, (UBaseType_t)item_bytes);
    return *out ? 0 : -1;
}

int rmb_port_queue_send(rmb_queue_t queue, const void *item, uint32_t ticks) {
    return xQueueSend(queue, item, ticks) == pdTRUE ? 0 : -1;
}

int rmb_port_queue_receive(rmb_queue_t queue, void *item, uint32_t ticks) {
    return xQueueReceive(queue, item, ticks) == pdTRUE ? 0 : -1;
}

int rmb_port_event_create(rmb_event_t *out) {
    *out = xEventGroupCreate();
    return *out ? 0 : -1;
}

void rmb_port_event_set(rmb_event_t events, uint32_t bits) {
    xEventGroupSetBits(events, bits);
}

void rmb_port_event_clear(rmb_event_t events, uint32_t bits) {
    xEventGroupClearBits(events, bits);
}

uint32_t rmb_port_event_get(rmb_event_t events) {
    return (uint32_t)xEventGroupGetBits(events);
}

uint32_t rmb_port_event_wait(rmb_event_t events, uint32_t bits, bool all, uint32_t ticks) {
    return (uint32_t)xEventGroupWaitBits(events, bits, pdFALSE, all ? pdTRUE : pdFALSE, ticks);
}

/* ── Watchdog ─────────────────────────────────────────────────────────── */
int rmb_port_wdt_configure(const rmb_wdt_config_t *config) {
    esp_task_wdt_config_t sdk = {
        .timeout_ms = config->timeout_ms,
        .idle_core_mask = config->idle_core_mask,
        .trigger_panic = config->trigger_panic,
    };
    esp_err_t rc = esp_task_wdt_reconfigure(&sdk);
    if (rc == ESP_ERR_INVALID_STATE)
        rc = esp_task_wdt_init(&sdk);
    return rc == ESP_OK ? 0 : -1;
}

int rmb_port_wdt_register_current(void) {
    return esp_task_wdt_add(NULL) == ESP_OK ? 0 : -1;
}

int rmb_port_wdt_feed(void) {
    return esp_task_wdt_reset() == ESP_OK ? 0 : -1;
}

/* ── Persistent (retained) memory ─────────────────────────────────────── */
/* One static region per request; the resident asks for exactly one journal. */
RMB_RETAINED static uint8_t retained_area[512];
void *rmb_port_retained(size_t bytes, size_t align) {
    uintptr_t start = ((uintptr_t)retained_area + (align - 1)) & ~(uintptr_t)(align - 1);
    if (bytes > sizeof retained_area ||
        start + bytes > (uintptr_t)retained_area + sizeof retained_area)
        return NULL;
    return (void *)start;
}

/* ── Business slot storage ────────────────────────────────────────────── */
static const esp_partition_t *slot(void) {
    return esp_partition_find_first(BUSINESS_TYPE, BUSINESS_SUBTYPE, BUSINESS_LABEL);
}

int rmb_port_slot_geometry(uint32_t *size, uint32_t *footer_size) {
    const esp_partition_t *partition = slot();
    if (!partition)
        return -1;
    if (partition->size != SLOT_SIZE || partition->address != SLOT_ADDRESS)
        return -1;
    if (size)
        *size = partition->size;
    if (footer_size)
        *footer_size = SLOT_FOOTER;
    return 0;
}

int rmb_port_slot_read(uint32_t offset, void *destination, uint32_t bytes) {
    const esp_partition_t *partition = slot();
    if (!partition)
        return -1;
    return esp_partition_read(partition, offset, destination, bytes) == ESP_OK ? 0 : -1;
}

int rmb_port_slot_write(uint32_t offset, const void *source, uint32_t bytes) {
    const esp_partition_t *partition = slot();
    if (!partition)
        return -1;
    return esp_partition_write(partition, offset, source, bytes) == ESP_OK ? 0 : -1;
}

int rmb_port_slot_erase(uint32_t offset, uint32_t bytes) {
    const esp_partition_t *partition = slot();
    if (!partition)
        return -1;
    return esp_partition_erase_range(partition, offset, bytes) == ESP_OK ? 0 : -1;
}

int rmb_port_slot_sha256(uint32_t offset, uint32_t bytes, uint8_t digest[32]) {
    uint8_t block[512];
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    int rc = mbedtls_sha256_starts(&context, 0);
    while (!rc && bytes) {
        uint32_t chunk = bytes > sizeof block ? sizeof block : bytes;
        if (rmb_port_slot_read(offset, block, chunk)) {
            mbedtls_sha256_free(&context);
            return -1;
        }
        rc = mbedtls_sha256_update(&context, block, chunk);
        offset += chunk;
        bytes -= chunk;
    }
    if (!rc)
        rc = mbedtls_sha256_finish(&context, digest);
    mbedtls_sha256_free(&context);
    return rc ? -1 : 0;
}

int rmb_port_slot_map(uint32_t *text_address) {
    const esp_partition_t *partition = slot();
    if (!partition)
        return -1;
    if (!mapped_base) {
        const void *base = NULL;
        esp_partition_mmap_handle_t handle;
        esp_err_t rc = esp_partition_mmap(partition, 0, partition->size, ESP_PARTITION_MMAP_INST,
                                          &base, &handle);
        if (rc != ESP_OK)
            return -1;
        if (reserved_base && (uintptr_t)base != reserved_base) {
            esp_partition_munmap(handle);
            return -1;
        }
        mapped_base = base;
        mapped_handle = handle;
        reserved_base = (uintptr_t)base;
    }
    if (text_address)
        *text_address = (uint32_t)reserved_base;
    return 0;
}

int rmb_port_slot_unmap(void) {
    if (mapped_base) {
        esp_partition_munmap(mapped_handle);
        mapped_base = NULL;
        mapped_handle = 0;
    }
    return 0;
}

int rmb_port_slot_lock(void) {
    /* ESP32-C3 SRAM PMS protection for the reserved data window is configured by
     * the linker/startup and stays locked; the XIP mapping is read/execute only.
     * Nothing to re-enable here, so report success only because the platform
     * guarantees it rather than silently skipping a requested lock. */
    return 0;
}

/* ── Base identity ────────────────────────────────────────────────────── */
int rmb_port_base_sha256(uint8_t digest[32]) {
    const esp_app_desc_t *description = esp_app_get_description();
    if (!description)
        return -1;
    memcpy(digest, description->app_elf_sha256, 32);
    return 0;
}
