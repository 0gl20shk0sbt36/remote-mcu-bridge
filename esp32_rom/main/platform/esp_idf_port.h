/* ESP-IDF/FreeRTOS binding for the resident port contract (rmb/port.h).
 *
 * This header is the only place that names platform types. Everything the
 * resident needs is either a FreeRTOS handle or a plain C type, so the port
 * header stays small enough to re-implement for another RTOS.
 */
#ifndef RMB_PLATFORM_ESP_IDF_PORT_H
#define RMB_PLATFORM_ESP_IDF_PORT_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

typedef portMUX_TYPE rmb_lock_t;
#define RMB_LOCK_INIT portMUX_INITIALIZER_UNLOCKED

typedef TaskHandle_t rmb_task_t;
typedef SemaphoreHandle_t rmb_mutex_t;
typedef SemaphoreHandle_t rmb_sem_t;
typedef QueueHandle_t rmb_queue_t;
typedef EventGroupHandle_t rmb_event_t;

/* RAM that survives a software reset but not a power cut. */
#define RMB_RETAINED RTC_NOINIT_ATTR

/* Module data window: ordinary internal DRAM, never executable. The resident
 * relies on this section staying out of IRAM/XIP. */
#define RMB_DRAM_RESERVE_SECTION __attribute__((section(".dram1.module_reserve"), aligned(16)))

#define RMB_WAIT_FOREVER portMAX_DELAY
#define RMB_WAIT_NOW 0
#define RMB_MS_TO_TICKS(ms) pdMS_TO_TICKS(ms)

typedef struct {
    uint32_t timeout_ms;
    uint32_t idle_core_mask;
    bool trigger_panic;
} rmb_wdt_config_t;

/* Compile-time configuration supplied by the ESP-IDF Kconfig defaults. */
#include "sdkconfig.h"
#define RMB_CONFIG_TOKEN CONFIG_RMB_TOKEN
#define RMB_CONFIG_SSID CONFIG_RMB_WIFI_SSID
#define RMB_CONFIG_PASSWORD CONFIG_RMB_WIFI_PASSWORD
#define RMB_CONFIG_PORT CONFIG_RMB_PORT
#define RMB_CONFIG_DISCOVERY_PORT CONFIG_RMB_DISCOVERY_PORT
#define RMB_CONFIG_LISTENER_STACK_BYTES CONFIG_RMB_LISTENER_STACK_BYTES

#endif /* RMB_PLATFORM_ESP_IDF_PORT_H */
