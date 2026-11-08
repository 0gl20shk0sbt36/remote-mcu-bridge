/* Map fixture ESP/FreeRTOS mocks onto the resident port contract.
 *
 * Include AFTER the fixture's own mocks: the macros below adapt their calling
 * conventions (FreeRTOS truthiness, ESP error codes) to rmb/port.h, so an
 * existing extraction fixture keeps its instrumentation while the production
 * code under test calls only the port.
 */
#pragma once
#include "rmb/port.h"

/* Production units log with a per-file TAG; fixtures need not define one. */
#ifndef TAG
#define TAG "rmb-native"
#endif

/* Configuration macros come from the platform header the fixture selected. */

#define rmb_port_now_us() esp_timer_get_time()
#define rmb_port_delay_ticks(t) vTaskDelay(t)
#define rmb_port_restart() esp_restart()
#define rmb_port_reset_reason() ((rmb_reset_reason)esp_reset_reason())
#define rmb_port_lock_enter(p) portENTER_CRITICAL(p)
#define rmb_port_lock_exit(p) portEXIT_CRITICAL(p)
#define rmb_port_in_isr() (xPortInIsrContext() != 0)
#define rmb_port_log(level, tag, ...) ((void)(level), (void)(tag))
#define rmb_port_wdt_register_current() (esp_task_wdt_add(NULL) == 0 ? 0 : -1)
#define rmb_port_wdt_feed() (esp_task_wdt_reset() == 0 ? 0 : -1)
#define rmb_port_wdt_configure(c) (esp_task_wdt_reconfigure(c) == 0 ? 0 : -1)
#define rmb_port_task_current() xTaskGetCurrentTaskHandle()
#define rmb_port_task_end() vTaskDelete(NULL)
#define rmb_port_task_terminate(t) vTaskDelete(t)
#define rmb_port_task_wait_notify() ulTaskNotifyTake(pdTRUE, portMAX_DELAY)
#define rmb_port_task_notify(t) xTaskNotifyGive(t)
#define rmb_port_task_stack_min_free() uxTaskGetStackHighWaterMark(NULL)
#define rmb_port_task_create(n, f, c, s, p, o) (xTaskCreate(f, n, s, c, p, o) == pdPASS ? 0 : -1)
int rmb_port_mutex_create(rmb_mutex_t *out) {
    rmb_mutex_t h = xSemaphoreCreateMutex();
    if (!h)
        return -1;
    *out = h;
    return 0;
}
#define rmb_port_mutex_take(m, t) (xSemaphoreTake(m, t) == pdTRUE ? 0 : -1)
#define rmb_port_mutex_give(m) xSemaphoreGive(m)
#define rmb_port_mutex_delete(m) vSemaphoreDelete(m)
int rmb_port_sem_create(rmb_sem_t *out) {
    rmb_sem_t h = xSemaphoreCreateBinary();
    if (!h)
        return -1;
    *out = h;
    return 0;
}
#define rmb_port_sem_take(s, t) (xSemaphoreTake(s, t) == pdTRUE ? 0 : -1)
#define rmb_port_sem_give(s) xSemaphoreGive(s)
#define rmb_port_sem_delete(s) vSemaphoreDelete(s)
int rmb_port_queue_create(uint32_t length, uint32_t item, rmb_queue_t *out) {
    rmb_queue_t h = xQueueCreate(length, item);
    if (!h)
        return -1;
    *out = h;
    return 0;
}
#define rmb_port_queue_send(q, i, t) (xQueueSend(q, i, t) == pdTRUE ? 0 : -1)
#define rmb_port_queue_receive(q, i, t) (xQueueReceive(q, i, t) == pdTRUE ? 0 : -1)
#define rmb_port_event_wait(e, b, a, t)                                                            \
    xEventGroupWaitBits(e, b, pdFALSE, (a) ? pdTRUE : pdFALSE, t)
