/* Shared reservation state. Critical sections contain no driver/network calls. */
static portMUX_TYPE relay_lock = portMUX_INITIALIZER_UNLOCKED;
static bool relay_paused, control_reserved;
static uint32_t relay_epoch = 1;
static unsigned relay_baud = 115200;
static const char *relay_target = "unknown";
static void relay_snapshot(bool *paused, uint32_t *epoch) {
    portENTER_CRITICAL(&relay_lock);
    *paused = relay_paused;
    *epoch = relay_epoch;
    portEXIT_CRITICAL(&relay_lock);
}
static bool control_acquire(void) {
    portENTER_CRITICAL(&relay_lock);
    if (control_reserved) {
        portEXIT_CRITICAL(&relay_lock);
        return false;
    }
    control_reserved = true;
    relay_paused = true;
    ++relay_epoch;
    portEXIT_CRITICAL(&relay_lock);
    /* The relay polls every tick, with no socket wait while owning UART. */
    int64_t until = host->now_us() + 1200000LL;
    bool acquired = false;
    while (host->now_us() < until) {
        service_progress();
        if (xSemaphoreTake(uart_owner, pdMS_TO_TICKS(50) ? pdMS_TO_TICKS(50) : 1) == pdTRUE) {
            acquired = true;
            break;
        }
    }
    if (acquired) {
        /* Do not reconfigure with legacy APP TX still on the wire. */
        if (uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(100)) == ESP_OK)
            return true;
        xSemaphoreGive(uart_owner);
    }
    portENTER_CRITICAL(&relay_lock);
    relay_paused = false;
    ++relay_epoch;
    control_reserved = false;
    portEXIT_CRITICAL(&relay_lock);
    return false;
}
static void control_release(void) {
    /* Never expose unread ROM ACKs to APP subscribers, including failures. */
    (void)uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(100));
    target_events(NULL);
    portENTER_CRITICAL(&relay_lock);
    unsigned baud = relay_baud;
    portEXIT_CRITICAL(&relay_lock);
    if (target_uart_mode(baud, false) != ESP_OK)
        relay_target = "uart-restore-failed";
    (void)uart_flush_input(UART_PORT);
    xSemaphoreGive(uart_owner);
    portENTER_CRITICAL(&relay_lock);
    relay_paused = false;
    ++relay_epoch;
    control_reserved = false;
    portEXIT_CRITICAL(&relay_lock);
}
