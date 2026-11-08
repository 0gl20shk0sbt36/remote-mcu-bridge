/* One task owns the socket. All sends are nonblocking, even with UART held. */
#define RELAY_TX_CAP 2048
struct relay_tx {
    uint8_t bytes[RELAY_TX_CAP];
    size_t used;
    int64_t deadline;
};
static bool relay_queue(struct relay_tx *q, unsigned type, const void *p, size_t n) {
    if (n > 1028 || q->used + n + 3 > sizeof q->bytes)
        return false;
    if (!q->used)
        q->deadline = esp_timer_get_time() + 1000000LL;
    q->bytes[q->used++] = (uint8_t)type;
    q->bytes[q->used++] = (uint8_t)(n >> 8);
    q->bytes[q->used++] = (uint8_t)n;
    memcpy(q->bytes + q->used, p, n);
    q->used += n;
    return true;
}
static bool relay_status(struct relay_tx *q, const char *event, uint32_t epoch) {
    char b[192];
    int n = snprintf(b, sizeof b, "{\"event\":\"%s\",\"epoch\":%lu,\"target\":\"%s\"}", event,
                     (unsigned long)epoch, relay_target);
    return n > 0 && (size_t)n < sizeof b && relay_queue(q, 2, b, (size_t)n);
}
static uint32_t relay_decode_epoch(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}
static void relay_encode_epoch(uint8_t *p, uint32_t epoch) {
    p[0] = (uint8_t)(epoch >> 24);
    p[1] = (uint8_t)(epoch >> 16);
    p[2] = (uint8_t)(epoch >> 8);
    p[3] = (uint8_t)epoch;
}
static void uart_relay_session(int fd, unsigned baud, bool framed) {
    bool owned = false, paused;
    uint32_t epoch;
    relay_snapshot(&paused, &epoch);
    if (!paused) {
        if (xSemaphoreTake(uart_owner, 0) != pdTRUE) {
            failure(fd, "UART busy");
            return;
        }
        owned = true;
        /* A control request can reserve between snapshot and mutex take. */
        relay_snapshot(&paused, &epoch);
        if (paused) {
            xSemaphoreGive(uart_owner);
            owned = false;
        } else if (target_uart_mode(baud, false) != ESP_OK)
            goto done;
    }
    if (!framed && paused) {
        failure(fd, "UART busy");
        goto done;
    }
    portENTER_CRITICAL(&relay_lock);
    relay_baud = baud;
    portEXIT_CRITICAL(&relay_lock);
    struct relay_tx tx = {0};
    char ack[112];
    int ack_n = framed ? snprintf(ack, sizeof ack,
                                  "{\"ok\":true,\"framed\":1,\"epoch\":%lu,\"paused\":%s}\n",
                                  (unsigned long)epoch, paused ? "true" : "false")
                       : snprintf(ack, sizeof ack, "{\"ok\":true}\n");
    memcpy(tx.bytes, ack, (size_t)ack_n);
    tx.used = (size_t)ack_n;
    tx.deadline = esp_timer_get_time() + 1000000LL;
    uint8_t input[1031];
    size_t used = 0, want = 3;
    int64_t input_deadline = 0;
    uint32_t observed = epoch;
    for (;;) {
        service_io_progress();
        relay_snapshot(&paused, &epoch);
        /* RELEASE BEFORE formatting/queueing/sending status. Never socket-wait. */
        if (paused && owned) {
            xSemaphoreGive(uart_owner);
            owned = false;
            if (!framed)
                break; /* Raw legacy cannot safely preserve generations. */
        }
        if (epoch != observed) {
            /* Every transition increments once; recover even fast operations
             * completed while this task was descheduled. Even=pause, odd=resume.
             * Queue exhaustion disconnects rather than silently losing STATUS. */
            bool queued = true;
            while (observed != epoch) {
                ++observed;
                if (!relay_status(&tx, (observed & 1) ? "resumed" : "paused", observed)) {
                    queued = false;
                    break;
                }
            }
            if (!queued)
                break;
        }
        if (!paused && !owned) {
            if (xSemaphoreTake(uart_owner, 0) == pdTRUE) {
                owned = true;
                relay_snapshot(&paused, &epoch);
                if (paused) {
                    xSemaphoreGive(uart_owner);
                    owned = false;
                    continue;
                }
                if (target_uart_mode(baud, false) != ESP_OK)
                    break;
            }
        }
        if (tx.used) {
            int n = host->net->write(fd, tx.bytes, tx.used, true);
            if (n > 0) {
                tx.used -= (size_t)n;
                memmove(tx.bytes, tx.bytes + n, tx.used);
            } else if (n == 0 || n != RMB_IO_AGAIN)
                break;
            if (tx.used && esp_timer_get_time() > tx.deadline)
                break;
        }
        int n = host->net->read(fd, input + used, framed ? want - used : sizeof input - 4, true);
        if (!n)
            break;
        if (n < 0 && n != RMB_IO_AGAIN)
            break;
        if (n > 0) {
            if (!used)
                input_deadline = esp_timer_get_time() + 3000000LL;
            used += (size_t)n;
            if (framed && used == 3 && want == 3) {
                size_t length = ((size_t)input[1] << 8) | input[2];
                if (input[0] != 1 || length < 4 || length > 1028)
                    break;
                want = 3 + length;
            }
            if (!framed || used == want) {
                relay_snapshot(&paused, &epoch);
                if (paused || !owned || (framed && relay_decode_epoch(input + 3) != epoch)) {
                    if (!framed || !relay_status(&tx, "write-rejected", epoch))
                        break;
                } else {
                    const uint8_t *data = framed ? input + 7 : input;
                    size_t length = framed ? used - 7 : used;
                    if (length && uart_write_bytes(UART_PORT, data, length) != (int)length)
                        break;
                    if (uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(100)) != ESP_OK)
                        break;
                }
                used = 0;
                want = 3;
            }
        }
        if (used && esp_timer_get_time() > input_deadline)
            break;
        /* No ROM RX reads while the control reservation is active. */
        relay_snapshot(&paused, &epoch);
        if (owned && !paused && tx.used < 512) {
            uint8_t data[260];
            n = uart_read_bytes(UART_PORT, data + 4, 256, 0);
            target_events(NULL);
            if (n < 0)
                break;
            if (n > 0) {
                if (framed) {
                    relay_encode_epoch(data, epoch);
                    if (!relay_queue(&tx, 1, data, (size_t)n + 4))
                        break;
                } else {
                    if (!tx.used)
                        tx.deadline = esp_timer_get_time() + 1000000LL;
                    memcpy(tx.bytes + tx.used, data + 4, (size_t)n);
                    tx.used += (size_t)n;
                }
            }
        }
        vTaskDelay(1);
    }
done:
    if (owned)
        xSemaphoreGive(uart_owner);
}
static void uart_session(int fd, unsigned baud) {
    uart_relay_session(fd, baud, false);
}
static void uart_framed_session(int fd, unsigned baud) {
    uart_relay_session(fd, baud, true);
}
