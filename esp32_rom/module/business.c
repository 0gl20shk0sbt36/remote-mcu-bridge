/* Trusted LAN only: plaintext authentication, no TLS. ESP-IDF 5.x / ESP32-C3. */
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "sdkconfig.h"
#include "cJSON.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "platform/esp_idf_io.h"
#if RMB_BUILTIN_SPI
#include "logic/spi_diagnostic.h"
#endif
#include "soc/uart_reg.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "rom.h"

static const char *TAG = "business";
#include "rmb/service.h"
static const rmb_host *host;
/* Only the serial control callback and its supervised worker share control.
 * No root task ever feeds either active callback lease. */
static int root_lease = -1, control_lease = -1, uart_lease = -1;
static _Atomic(TaskHandle_t) uart_callback_task;
static void service_progress(void) {
    if (control_lease >= 0)
        (void)host->watchdog_feed(control_lease, (uint32_t)host->now_us());
    host->progress();
}
static void service_io_progress(void) {
    int lease = xTaskGetCurrentTaskHandle() ==
                        atomic_load_explicit(&uart_callback_task, memory_order_relaxed)
                    ? uart_lease
                    : control_lease;
    if (lease >= 0)
        (void)host->watchdog_feed(lease, (uint32_t)host->now_us());
    host->progress();
}
static void service_events(void *context) {
    (void)context;
    for (;;) {
        /* Running this idle/event-loop iteration is root progress, not callback progress. */
        (void)host->watchdog_feed(root_lease, (uint32_t)host->now_us());
        vTaskDelay(pdMS_TO_TICKS(250) ? pdMS_TO_TICKS(250) : 1);
    }
}
#include "service_net.h"
static const rom_device *device;
static const esp_partition_t *staging;
static SemaphoreHandle_t uart_owner;
#define REQUEST_MAX 1024
#define HEADER_US 10000000LL
#define UPLOAD_US 180000000LL
#define supervisor_progress() service_progress()
typedef enum { JOB_VALIDATE, JOB_DOWNLOAD, JOB_PROBE, JOB_RESTART } rom_job_kind;
typedef struct {
    rom_job_kind kind;
    const rom_io *io;
    const rom_device *device;
    const rom_source *source;
    uint32_t size, crc;
    int ram;
    rom_probe_result *result;
    rom_trace_fn trace;
    void *trace_ctx;
} rom_job;
static int rom_work(void *context) {
    rom_job *j = context;
    service_progress();
    switch (j->kind) {
    case JOB_VALIDATE:
        return rom_validate(j->device, j->source, j->size, j->crc, j->ram);
    case JOB_DOWNLOAD:
        return rom_download_traced(j->io, j->device, j->source, j->size, j->crc, j->ram, j->trace,
                                   j->trace_ctx);
    case JOB_PROBE:
        return rom_probe(j->io, j->device, j->result, j->trace, j->trace_ctx);
    case JOB_RESTART:
        return rom_restart(j->io);
    }
    return ROM_E_ARG;
}
/* Borrowed contexts remain live until completion or whole-chip reset. */
static int supervised(rom_job *j) {
    return host->run(rom_work, j, 1800000000LL);
}
static int supervisor_validate(const rom_device *d, const rom_source *s, uint32_t n, uint32_t c,
                               int r) {
    rom_job j = {.kind = JOB_VALIDATE, .device = d, .source = s, .size = n, .crc = c, .ram = r};
    return supervised(&j);
}
static int supervisor_download(const rom_io *i, const rom_device *d, const rom_source *s,
                               uint32_t n, uint32_t c, int r, rom_trace_fn f, void *x) {
    rom_job j = {.kind = JOB_DOWNLOAD,
                 .io = i,
                 .device = d,
                 .source = s,
                 .size = n,
                 .crc = c,
                 .ram = r,
                 .trace = f,
                 .trace_ctx = x};
    return supervised(&j);
}
static int supervisor_probe(const rom_io *i, const rom_device *d, rom_probe_result *r,
                            rom_trace_fn f, void *x) {
    rom_job j = {.kind = JOB_PROBE, .io = i, .device = d, .result = r, .trace = f, .trace_ctx = x};
    return supervised(&j);
}
static int supervisor_restart(const rom_io *i) {
    rom_job j = {.kind = JOB_RESTART, .io = i};
    return supervised(&j);
}
typedef struct {
    const char *stage, *phase;
    unsigned command_index, tx_bytes, rx_bytes;
    int ack_byte;
    bool probe, prepared;
    int64_t sync_started, first_byte_us;
    bool raw_enabled;
    int64_t raw_started;
    unsigned raw_tx, raw_rx;
    unsigned frame_errors, parity_errors, fifo_overflows, buffer_full;
    size_t stale_count;
    int rx_level, boot0_level, nrst_level;
} target_diag;
static QueueHandle_t uart_events;
/* Same control task is the ONLY queue and RX-ring consumer. Events carry no
 * payload and UART_DATA is notification only; never read RX in another task.
 * No xQueueReset: it could race the ISR and hide an error. Bounded snapshots. */
static void target_events(target_diag *d) {
    uart_event_t e;
    for (unsigned k = 0; k < 64 && xQueueReceive(uart_events, &e, 0) == pdTRUE; k++) {
        if (!d)
            continue;
        switch (e.type) {
        case UART_FRAME_ERR:
            d->frame_errors++;
            break;
        case UART_PARITY_ERR:
            d->parity_errors++;
            break;
        case UART_FIFO_OVF:
            d->fifo_overflows++;
            break;
        case UART_BUFFER_FULL:
            d->buffer_full++;
            break;
        default:
            break;
        }
    }
}
/* Only UART1 callbacks/snapshots feed this logger; ESP_LOG uses console UART0/
 * USB, never UART1. Counts describe driver-accepted TX / task-read RX, NOT wire
 * timestamps or proof of physical delivery. Single control-task ownership. */
#define TARGET_RX_BUFFER_SIZE 2048
#define TARGET_HEX_CHUNK 32
static uint8_t target_snapshot[TARGET_RX_BUFFER_SIZE];
static void target_raw(target_diag *d, bool tx, const char *scope, const uint8_t *data,
                       size_t length, int64_t observed) {
    if (!d || !d->raw_enabled)
        return;
    for (size_t offset = 0; offset < length;) {
        size_t n = length - offset;
        if (n > TARGET_HEX_CHUNK)
            n = TARGET_HEX_CHUNK;
        char hex[TARGET_HEX_CHUNK * 3 + 1];
        for (size_t k = 0; k < n; k++)
            snprintf(hex + 3 * k, sizeof(hex) - 3 * k, "%02x ", data[offset + k]);
        if (tx)
            d->raw_tx += (unsigned)n;
        else
            d->raw_rx += (unsigned)n;
        ESP_LOGI(TAG, "UART1 %s %s task_rel_us=%lld n=%u total_tx=%u total_rx=%u hex=[%s]",
                 tx ? "TX" : "RX", scope, (long long)(observed - d->raw_started), (unsigned)n,
                 d->raw_tx, d->raw_rx, hex);
        offset += n;
    }
}
static void target_raw_stop(target_diag *d, const char *reason) {
    if (!d || !d->raw_enabled)
        return;
    d->raw_enabled = false; /* Before command TX, including failed first write. */
    ESP_LOGI(
        TAG,
        "UART1 raw trace STOP %s task_rel_us=%lld total_tx=%u total_rx=%u; no firmware hex follows",
        reason, (long long)(esp_timer_get_time() - d->raw_started), d->raw_tx, d->raw_rx);
}
static int target_snapshot_drain(target_diag *d, const char *scope) {
    size_t pending = 0, remaining = 0, captured = 0;
    target_events(d);
    if (uart_get_buffered_data_len(UART_PORT, &pending) != ESP_OK)
        return -1;
    if (!strcmp(scope, "pre-sync"))
        d->stale_count = pending;
    size_t limit = pending < sizeof target_snapshot ? pending : sizeof target_snapshot;
    /* Capture the finite initial pending snapshot BEFORE slow console printing.
     * No flush: new arrivals remain in RX, never silently thrown away. No loop
     * until idle, so continuous noise cannot make this drain run forever. */
    int error = 0;
    while (captured < limit) {
        size_t n = limit - captured;
        if (n > TARGET_HEX_CHUNK)
            n = TARGET_HEX_CHUNK;
        int got = uart_read_bytes(UART_PORT, target_snapshot + captured, n, 0);
        if (got <= 0) {
            error = got < 0 ? -1 : 0;
            break;
        }
        captured += (size_t)got;
    }
    int64_t observed = esp_timer_get_time();
    target_raw(d, false, scope, target_snapshot, captured, observed);
    if (uart_get_buffered_data_len(UART_PORT, &remaining) != ESP_OK)
        error = -1;
    target_events(d);
    ESP_LOGI(TAG,
             "UART1 %s finite snapshot pending=%u limit=%u captured=%u uncaptured_initial=%u "
             "pending_now=%u io_error=%d; new arrivals retained; FIFO/ring overflow or event-queue "
             "loss cannot be reconstructed",
             scope, (unsigned)pending, (unsigned)limit, (unsigned)captured,
             (unsigned)(pending - captured), (unsigned)remaining, error);
    return error;
}
static int target_raw_start(target_diag *d) {
    d->raw_started = esp_timer_get_time();
    d->raw_enabled = true;
    d->raw_tx = d->raw_rx = 0;
    ESP_LOGI(TAG, "UART1 raw trace START: console only; 32-byte hex chunks; task-observed relative "
                  "times, TX driver accepted not wire proof; console throughput overhead may "
                  "perturb timing/overflow; no per-byte delays");
    return target_snapshot_drain(d, "operation-start");
}
static int target_prepare(target_diag *d) {
    if (target_snapshot_drain(d, "pre-sync"))
        return -1;
    d->rx_level = gpio_get_level(CONFIG_ROM_SERVICE_RX);
    d->boot0_level = gpio_get_level(CONFIG_ROM_SERVICE_BOOT0);
    d->nrst_level = gpio_get_level(CONFIG_ROM_SERVICE_NRST);
    ESP_LOGI(TAG, "Target pre-sync after reset wait: full bounded snapshot logged above; no unseen "
                  "input flushed");
    ESP_LOGI(TAG,
             "Pre-sync UART errors: frame=%u parity=%u fifo=%u full=%u; resetting counters for "
             "post-sync interval (queue may undercount)",
             d->frame_errors, d->parity_errors, d->fifo_overflows, d->buffer_full);
    d->frame_errors = d->parity_errors = d->fifo_overflows = d->buffer_full = 0;
    ESP_LOGI(TAG,
             "ESP GPIO local pad readback RX=%d BOOT0=%d NRST=%d; NOT remote STM32 measurement; "
             "BOOT1 %s",
             d->rx_level, d->boot0_level, d->nrst_level,
             CONFIG_ROM_SERVICE_BOOT1 < 0 ? "undriven/unmeasured"
                                          : "configured low; remote unmeasured");
    d->prepared = true;
    return 0;
}
static void target_trace(void *ctx, const char *stage, const char *phase, unsigned index,
                         uint32_t address, int rc, int byte) {
    supervisor_progress();
    target_diag *d = ctx;
    if (!strcmp(stage, "write") && !strcmp(phase, "command"))
        target_raw_stop(d, "before first Write Memory (0x31) command/address/payload");
    /* Keep the failing ACK OR semantic phase when final result follows it. */
    if (strcmp(phase, "result")) {
        d->stage = stage;
        d->phase = phase;
        d->ack_byte = byte;
    }
    d->command_index = index;
    if (!strcmp(phase, "bootloader-version")) {
        ESP_LOGI(TAG, "ROM bootloader version=0x%02lx", (unsigned long)address);
        return;
    }
    if (!strcmp(phase, "version-options") || !strcmp(phase, "version-zero") ||
        !strcmp(phase, "version-mismatch")) {
        if (!strcmp(phase, "version-mismatch") && !rc)
            ESP_LOGW(TAG, "Different nonzero Get/GetVersion values accepted as compatibility "
                          "warning; ACK/ID/capability/image checks remain enforced");
        ESP_LOGI(TAG,
                 "ROM Get prior version=0x%02x; GetVersion v[0]=0x%02x v[1]=0x%02x v[2]=0x%02x "
                 "phase=%s rc=%d (semantic, not ACK)",
                 (unsigned)(address >> 24), (unsigned)((address >> 16) & 255),
                 (unsigned)((address >> 8) & 255), (unsigned)(address & 255), phase, rc);
        return;
    }
    if (!strcmp(phase, "capabilities-mask")) {
        ESP_LOGI(
            TAG,
            "ROM capabilities Get=%u Version=%u ID=%u Read=%u Write=%u Go=%u Erase43=%u Erase44=%u",
            (unsigned)(address & 1), (unsigned)((address >> 1) & 1), (unsigned)((address >> 2) & 1),
            (unsigned)((address >> 3) & 1), (unsigned)((address >> 4) & 1),
            (unsigned)((address >> 5) & 1), (unsigned)((address >> 6) & 1),
            (unsigned)((address >> 7) & 1));
        return;
    }
    if (!strcmp(phase, "device-id")) {
        ESP_LOGI(TAG, "ROM GetID PID=0x%04lx (family, not capacity)", (unsigned long)address);
        return;
    }
    if (!strcmp(phase, "covered-erase-units")) {
        ESP_LOGI(TAG, "ROM erase covered units=%lu; no mass erase", (unsigned long)address);
        return;
    }
    bool block = !strcmp(stage, "write") || !strcmp(stage, "read");
    if (rc) {
        if (byte >= 0)
            ESP_LOGE(
                TAG, "ROM %s/%s cmd=%u addr=0x%08lx rc=%d ACK=0x%02x expected=0x79 tx=%u rx=%u",
                stage, phase, index, (unsigned long)address, rc, byte, d->tx_bytes, d->rx_bytes);
        else
            ESP_LOGE(TAG, "ROM %s/%s cmd=%u addr=0x%08lx rc=%d (%s) tx=%u rx=%u", stage, phase,
                     index, (unsigned long)address, rc, rom_strerror(rc), d->tx_bytes, d->rx_bytes);
    } else if (!block || !(address & 0xfffU) || !strcmp(phase, "verify-complete")) {
        if (byte >= 0)
            ESP_LOGI(TAG, "ROM %s/%s cmd=%u addr=0x%08lx ACK=0x%02x", stage, phase, index,
                     (unsigned long)address, byte);
        else
            ESP_LOGI(TAG, "ROM %s/%s cmd=%u value_or_addr=0x%08lx", stage, phase, index,
                     (unsigned long)address);
    }
}
/* UART callbacks return transferred byte count; core handles short I/O. */
static int target_tx(void *ctx, const uint8_t *data, size_t length) {
    supervisor_progress();
    target_diag *d = ctx;
    if (d && !d->prepared) {
        /* Only the initial sync may trigger draining, never protocol data. */
        if (length != 1 || data[0] != 0x7f)
            return -1;
        if (target_prepare(d))
            return -1;
        d->sync_started = esp_timer_get_time();
    }
    /* Defense in depth: stage metadata, NOT searching arbitrary data for 0x31. */
    if (d && !strcmp(d->stage, "write"))
        target_raw_stop(d, "write-stage TX safety boundary");
    target_events(d);
    int n = uart_write_bytes(UART_PORT, data, length);
    if (n <= 0 || (size_t)n > length)
        return n <= 0 ? n : -1;
    if (d)
        d->tx_bytes += (unsigned)n;
    int64_t observed = esp_timer_get_time();
    esp_err_t done = uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(2000));
    target_raw(d, true, "driver-accepted", data, (size_t)n, observed);
    if (done != ESP_OK)
        ESP_LOGW(
            TAG,
            "UART1 TX accepted=%d requested=%u; completion wait failed; physical delivery unknown",
            n, (unsigned)length);
    return done == ESP_OK ? n : -1;
}
static int target_rx(void *ctx, uint8_t *data, size_t length, uint32_t timeout_ms) {
    target_diag *d = ctx;
    size_t total = length;
    int64_t end = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    while (length) {
        int64_t remaining = end - esp_timer_get_time();
        if (remaining <= 0)
            return (int)(total - length);
        supervisor_progress();
        bool final_wait = remaining <= 250000;
        if (remaining > 250000)
            remaining = 250000;
        TickType_t ticks = pdMS_TO_TICKS((remaining + 999) / 1000);
        int n = uart_read_bytes(UART_PORT, data, length, ticks ? ticks : 1);
        target_events(d);
        if (n < 0)
            return total != length ? (int)(total - length) : n;
        if (n == 0) {
            if (final_wait)
                return (int)(total - length);
            continue;
        }
        if (d && d->first_byte_us < 0)
            d->first_byte_us = esp_timer_get_time() - d->sync_started;
        if (d)
            d->rx_bytes += (unsigned)n;
        target_raw(d, false, "protocol", data, (size_t)n, esp_timer_get_time());
        data += n;
        length -= (size_t)n;
    }
    return (int)total;
}
/* Read the pad input path, not merely the output latch. INPUT_OUTPUT modes
 * are set during initialization. Digital readback is NOT an analog voltage
 * measurement and is NOT a measurement at the remote STM32 pad. */
static void target_pad_report(const char *phase, int boot_expected, int nrst_expected) {
    int boot_actual = gpio_get_level(CONFIG_ROM_SERVICE_BOOT0);
    int nrst_actual = gpio_get_level(CONFIG_ROM_SERVICE_NRST);
    ESP_LOGI(TAG,
             "PAD %s: BOOT0 GPIO%d expected=%d actual=%d; NRST GPIO%d expected=%d actual=%d; "
             "ESP-local digital only",
             phase, CONFIG_ROM_SERVICE_BOOT0, boot_expected, boot_actual, CONFIG_ROM_SERVICE_NRST,
             nrst_expected, nrst_actual);
    if ((boot_expected >= 0 && boot_actual != boot_expected) ||
        (nrst_expected >= 0 && nrst_actual != nrst_expected))
        ESP_LOGW(TAG,
                 "PAD mismatch: check external drive/load/pin mapping; no voltage measurement");
}
static int target_boot(void *ctx, int rom_mode) {
    (void)ctx;
    ESP_LOGI(TAG, "Target BOOT0 drive=%d BOOT1=%s", rom_mode ? 1 : 0,
             CONFIG_ROM_SERVICE_BOOT1 < 0 ? "undriven/unmeasured" : "drive low; remote unmeasured");
    if (CONFIG_ROM_SERVICE_BOOT1 >= 0 && gpio_set_level(CONFIG_ROM_SERVICE_BOOT1, 0) != ESP_OK)
        return -1;
    int expected = rom_mode ? 1 : 0;
    if (gpio_set_level(CONFIG_ROM_SERVICE_BOOT0, expected) != ESP_OK)
        return -1;
    target_pad_report("BOOT0-set immediate", expected, -1);
    vTaskDelay(pdMS_TO_TICKS(10));
    target_pad_report("BOOT0-set settled", expected, -1);
    return 0;
}
static int target_reset(void *ctx) {
    target_diag *d = ctx;
    /* BOOT0 established by core; undriven BOOT1 must be strapped externally. */
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_LOGI(TAG, "Target NRST=0");
    if (gpio_set_level(CONFIG_ROM_SERVICE_NRST, 0) != ESP_OK)
        return -1;
    vTaskDelay(pdMS_TO_TICKS(20));
    target_pad_report("NRST-held-low", -1, 0);
    if (d)
        d->prepared = false; /* Both paths prepare AFTER all reset settling. */
    ESP_LOGI(TAG, "Target NRST=1");
    if (gpio_set_level(CONFIG_ROM_SERVICE_NRST, 1) != ESP_OK)
        return -1;
    vTaskDelay(pdMS_TO_TICKS(100));
    target_pad_report("NRST-released", -1, 1);
    return 0;
}
static void target_sleep(void *ctx, uint32_t ms) {
    (void)ctx;
    do {
        supervisor_progress();
        uint32_t part = ms > 250 ? 250 : ms;
        TickType_t ticks = pdMS_TO_TICKS(part);
        vTaskDelay(ticks ? ticks : 1);
        ms -= part;
    } while (ms);
}
static esp_err_t target_uart_mode(unsigned baud, bool rom_mode) {
    return esp_io_uart_mode(baud, rom_mode);
}
#include "uart_priority.inc"
static void service_status(int fd) {
    char report[512];
    bool paused;
    uint32_t epoch;
    relay_snapshot(&paused, &epoch);
    snprintf(report, sizeof report,
             "{\"ok\":true,\"device\":\"%s\",\"flash_size\":%lu,\"image_max\":%lu,\"staging_size\":"
             "%lu,\"uart_paused\":%s,\"epoch\":%lu,\"target\":\"%s\"}\n",
             device->name, (unsigned long)device->flash_size, (unsigned long)device->image_max,
             (unsigned long)staging->size, paused ? "true" : "false", (unsigned long)epoch,
             relay_target);
    reply(fd, report);
}
static bool stream_auth_ex(int fd, bool uart_mode, unsigned *baud, bool *history, bool *framed) {
    *framed = false;
    char line[REQUEST_MAX + 1];
    size_t used = 0;
    int64_t deadline = esp_timer_get_time() + HEADER_US;
    while (used < REQUEST_MAX) {
        if (!socket_exact(fd, line + used, 1, false, deadline))
            return false;
        if (!line[used]) {
            failure(fd, "invalid JSON");
            return false;
        }
        if (line[used++] == '\n')
            break;
    }
    if (!used || line[used - 1] != '\n') {
        failure(fd, "request too long");
        return false;
    }
    line[used] = 0;
    cJSON *r = cJSON_ParseWithLengthOpts(line, used + 1, NULL, true);
    bool valid = cJSON_IsObject(r) && !strstr(line, "\\u0000");
    unsigned seen = 0;
    cJSON *item;
    cJSON_ArrayForEach(item, r) {
        if (!item->string) {
            valid = false;
            continue;
        }
        unsigned bit = !strcmp(item->string, "token")                   ? 1
                       : !strcmp(item->string, "op")                    ? 2
                       : uart_mode && !strcmp(item->string, "baud")     ? 4
                       : !uart_mode && !strcmp(item->string, "history") ? 8
                                                                        : 0;
        if (!bit || (seen & bit))
            valid = false;
        seen |= bit;
    }
    cJSON *token = cJSON_GetObjectItemCaseSensitive(r, "token");
    cJSON *op = cJSON_GetObjectItemCaseSensitive(r, "op");
    valid = valid && cJSON_IsString(token) && authenticated(token->valuestring) &&
            cJSON_IsString(op) &&
            (!strcmp(op->valuestring, uart_mode ? "uart" : "logs") ||
             (uart_mode && !strcmp(op->valuestring, "uart-framed")));
    *framed = valid && uart_mode && !strcmp(op->valuestring, "uart-framed");
    *baud = 115200;
    *history = true;
    if (uart_mode && (seen & 4)) {
        uint32_t value = 0;
        valid = valid && number(cJSON_GetObjectItemCaseSensitive(r, "baud"), &value) &&
                stream_baud_allowed(value);
        *baud = (unsigned)value;
    }
    if (!uart_mode && (seen & 8)) {
        item = cJSON_GetObjectItemCaseSensitive(r, "history");
        valid = valid && cJSON_IsBool(item);
        *history = cJSON_IsTrue(item);
    }
    cJSON_Delete(r);
    if (!valid)
        failure(fd, "authentication or request rejected");
    return valid;
}
static bool stream_auth(int fd, bool uart_mode, unsigned *baud, bool *history) {
    bool framed;
    return stream_auth_ex(fd, uart_mode, baud, history, &framed);
}
static bool stream_send(int fd, const void *bytes, size_t n) {
    return socket_exact(fd, (void *)bytes, n, true, esp_timer_get_time() + 1000000LL);
}

#include "uart_framed.inc"

#if RMB_BUILTIN_SPI
static uint32_t spi_test_seq = 1;
static int64_t spi_now(void *ctx) {
    (void)ctx;
    return esp_timer_get_time();
}
static void spi_progress(void *ctx) {
    (void)ctx;
    supervisor_progress();
}
static int spi_open(void *ctx, uint32_t hz, const char **error) {
    (void)ctx;
    return esp_io_spi_open(hz, error);
}
static int spi_transfer(void *ctx, const uint8_t tx[64], uint8_t rx[64], const char **error) {
    (void)ctx;
    return esp_io_spi_transfer(tx, rx, error);
}
static int spi_close(void *ctx) {
    (void)ctx;
    return esp_io_spi_close();
}
static bool spi_busy(void *ctx) {
    (void)ctx;
    return esp_io_spi_busy();
}
static bool spi_acquire(void *ctx) {
    (void)ctx;
    return control_acquire();
}
static void spi_release(void *ctx) {
    (void)ctx;
    control_release();
}
static int spi_prepare(void *ctx, const char **stage, const char **error) {
    (void)ctx;
    relay_target = "app";
    int rc = target_uart_mode(115200, false);
    if (rc == ESP_OK)
        rc = uart_set_word_length(UART_PORT, UART_DATA_8_BITS);
    if (rc == ESP_OK)
        rc = uart_set_stop_bits(UART_PORT, UART_STOP_BITS_1);
    if (rc != ESP_OK) {
        *error = "115200 8N1 setup";
        return rc;
    }
    *stage = "uart-drain";
    target_diag drain = {0};
    if (target_snapshot_drain(&drain, "spi-stale")) {
        *error = "bounded stale drain";
        return -1;
    }
    return 0;
}
static int spi_arm(void *ctx, uint32_t seq) {
    (void)ctx;
    char arm[48];
    int length = snprintf(arm, sizeof arm, "SPI ARM %lu\n", (unsigned long)seq);
    return uart_write_bytes(UART_PORT, arm, (size_t)length) != length ||
                   uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(100)) != ESP_OK
               ? -1
               : 0;
}
/* Framing belongs to the UART adapter. Pure logic matches complete lines. */
static int spi_read_line(void *ctx, char *line, size_t capacity, int64_t deadline) {
    (void)ctx;
    size_t used = 0;
    bool overflow = false;
    while (esp_timer_get_time() < deadline) {
        supervisor_progress();
        uint8_t b;
        int got = uart_read_bytes(UART_PORT, &b, 1, pdMS_TO_TICKS(10) ? pdMS_TO_TICKS(10) : 1);
        if (got < 0)
            return -1;
        if (!got)
            continue;
        if (b == '\n') {
            if (!overflow) {
                if (used && line[used - 1] == '\r')
                    used--;
                line[used] = 0;
                return 1;
            }
            used = 0;
            overflow = false;
        } else if (used < capacity - 1 && b)
            line[used++] = (char)b;
        else
            overflow = true;
    }
    return 0;
}
static void spi_pad_sample(int fd, unsigned pull) {
    if (!control_acquire()) {
        failure(fd, "UART busy");
        return;
    }
    esp_io_spi_pad_result r = {0};
    int rc = esp_io_spi_pad_sample(pull, &r);
    control_release();
    if (rc == ESP_ERR_INVALID_STATE) {
        failure(fd, "SPI bus/device/pending active; no GPIO changes");
        return;
    }
    char report[1000];
    snprintf(
        report, sizeof report,
        "{\"ok\":%s,\"op\":\"spi-pad-sample\",\"rc\":%d,\"gpio\":0,\"scope\":\"ESP local digital "
        "pad input; not analog or remote "
        "STM32\",\"pull\":\"%s\",\"samples\":%u,\"low\":%u,\"high\":%u,\"spacing\":\"at least 1 "
        "RTOS tick, requested "
        "1ms\",\"before_level\":%d,\"before_iomux\":%lu,\"sample_iomux\":%lu,\"fspiq_matrix\":%lu,"
        "\"matrix_source\":%lu,\"matrix_selected\":%s,\"matrix_inverted\":%s,\"restored_iomux\":%"
        "lu,\"restore_policy\":\"input-only no-pull; inspect rc and restored_iomux\"}\n",
        rc == ESP_OK ? "true" : "false", rc,
        pull == 1   ? "up"
        : pull == 2 ? "down"
                    : "none",
        r.low + r.high, r.low, r.high, r.before.level, (unsigned long)r.before.iomux,
        (unsigned long)r.sampled.iomux, (unsigned long)r.sampled.matrix,
        (unsigned long)(r.sampled.matrix & 31), r.sampled.matrix & 64 ? "true" : "false",
        r.sampled.matrix & 32 ? "true" : "false", (unsigned long)r.restored.iomux);
    reply(fd, report);
}

static void spi_test(int fd, uint32_t count, uint32_t hz) {
    const spi_diagnostic_io io = {.now_us = spi_now,
                                  .progress = spi_progress,
                                  .open = spi_open,
                                  .transfer = spi_transfer,
                                  .close = spi_close,
                                  .busy = spi_busy,
                                  .acquire = spi_acquire,
                                  .release = spi_release,
                                  .prepare = spi_prepare,
                                  .arm = spi_arm,
                                  .read_line = spi_read_line};
    spi_diagnostic_result r;
    spi_diagnostic_run(&io, count, hz, &spi_test_seq, &r);
    char report[2200];
    snprintf(report, sizeof report,
             "{\"ok\":%s,\"op\":\"spi-test\",\"stage\":\"%s\",\"error\":\"%s\",\"rc\":%d,\"cleanup_"
             "rc\":%d,\"count\":%lu,\"completed\":%u,\"hz\":%lu,\"last_seq\":%lu,\"bytes_per_"
             "transaction\":64,\"esp_rx_verified\":%u,\"stm_rx_verified\":%u,\"rx_mismatch_bytes\":"
             "%u,\"elapsed_ms\":%lld,\"deadline_ms\":%lld,\"spi_host\":2,\"dma\":false}\n",
             r.ok ? "true" : "false", r.stage, r.error, r.rc, r.cleanup_rc, (unsigned long)count,
             r.completed, (unsigned long)hz, (unsigned long)r.last_seq, r.esp_rx_verified,
             r.stm_rx_verified, r.rx_mismatch_bytes, (long long)r.elapsed_ms,
             (long long)r.deadline_ms);
    /* Bounded evidence does not change result/STM error semantics. */
    size_t report_len = strlen(report);
    if (report_len >= 2)
        snprintf(
            report + report_len - 2, sizeof(report) - report_len + 2,
            ",\"rx_failure_captured\":%s,\"rx_failure_seq\":%lu,\"rx_first16_hex\":\"%s\","
            "\"expected_first16_hex\":\"%s\",\"first_mismatch_index\":%d,\"first_mismatch_actual\":"
            "%d,\"first_mismatch_expected\":%d,\"rx_min\":%d,\"rx_max\":%d,\"rx_constant\":%s,\"rx_"
            "scope\":\"ESP driver RX bytes, not remote pad measurement\"}\n",
            r.rx_failure_captured ? "true" : "false", (unsigned long)r.rx_failure_seq,
            r.rx_first16_hex, r.expected_first16_hex,
            r.rx_failure_captured ? (int)r.first_mismatch_index : -1,
            r.rx_failure_captured ? (int)r.first_mismatch_actual : -1,
            r.rx_failure_captured ? (int)r.first_mismatch_expected : -1,
            r.rx_failure_captured ? (int)r.rx_min : -1, r.rx_failure_captured ? (int)r.rx_max : -1,
            r.rx_constant ? "true" : "false");
    esp_io_spi_pad_snapshot before, during;
    esp_io_spi_pad_evidence(&before, &during);
    report_len = strlen(report);
    if (report_len >= 2)
        snprintf(report + report_len - 2, sizeof(report) - report_len + 2,
                 ",\"pad_scope\":\"ESP digital snapshot before init / before last transfer, not "
                 "waveform\",\"pad_before_level\":%d,\"pad_before_iomux\":%lu,\"pad_before_"
                 "matrix\":%lu,\"pad_during_level\":%d,\"pad_during_iomux\":%lu,\"pad_during_"
                 "matrix\":%lu,\"pad_during_matrix_source\":%lu,\"pad_during_matrix_selected\":%s,"
                 "\"pad_during_matrix_inverted\":%s}\n",
                 before.level, (unsigned long)before.iomux, (unsigned long)before.matrix,
                 during.level, (unsigned long)during.iomux, (unsigned long)during.matrix,
                 (unsigned long)(during.matrix & 31), during.matrix & 64 ? "true" : "false",
                 during.matrix & 32 ? "true" : "false");
    reply(fd, report);
}

#endif /* RMB_BUILTIN_SPI */

static int source_read(void *ctx, uint32_t offset, uint8_t *data, size_t length) {
    supervisor_progress();
    const esp_partition_t *p = ctx;
    if (offset > p->size || length > p->size - offset)
        return -1;
    return esp_partition_read(p, offset, data, length) == ESP_OK ? 0 : -1;
}
static void serve(int fd, bool *owned) {
    int64_t started = esp_timer_get_time();
    char line[REQUEST_MAX + 1];
    size_t used = 0;
    int64_t deadline = esp_timer_get_time() + HEADER_US;
    /* One-byte reads intentionally leave pipelined payload untouched. */
    while (used < REQUEST_MAX) {
        if (!socket_exact(fd, line + used, 1, false, deadline)) {
            failure(fd, "request timeout or disconnect");
            return;
        }
        if (line[used] == '\0') {
            failure(fd, "NUL in JSON request");
            return;
        }
        if (line[used++] == '\n')
            break;
    }
    if (!used || line[used - 1] != '\n') {
        failure(fd, "request exceeds 1024 bytes including newline");
        return;
    }
    line[used] = 0;
    const char *end = NULL;
    cJSON *request = cJSON_ParseWithLengthOpts(line, used + 1, &end, true);
    if (!request || !cJSON_IsObject(request)) {
        cJSON_Delete(request);
        failure(fd, "invalid JSON");
        return;
    }
    /* Reject duplicates, unknown keys, and embedded NUL escapes (cJSON strings are C strings). */
    bool valid = strstr(line, "\\u0000") == NULL;
    unsigned seen = 0;
    cJSON *item;
    cJSON_ArrayForEach(item, request) {
        unsigned bit = !strcmp(item->string, "token")   ? 1
                       : !strcmp(item->string, "op")    ? 2
                       : !strcmp(item->string, "size")  ? 4
                       : !strcmp(item->string, "crc32") ? 8
                       : !strcmp(item->string, "count") ? 16
                       : !strcmp(item->string, "hz")    ? 32
                       : !strcmp(item->string, "pull")  ? 64
                                                        : 0;
        if (!bit || (seen & bit))
            valid = false;
        seen |= bit;
    }
    cJSON *token = cJSON_GetObjectItemCaseSensitive(request, "token");
    cJSON *op = cJSON_GetObjectItemCaseSensitive(request, "op");
    if (!valid || !cJSON_IsString(token) || !authenticated(token->valuestring)) {
        ESP_LOGW(TAG, "Authentication/request rejected; no candidate logged");
        cJSON_Delete(request);
        failure(fd, "authentication or request rejected");
        return;
    }
    ESP_LOGI(TAG, "Authentication accepted");
    if (!cJSON_IsString(op)) {
        cJSON_Delete(request);
        failure(fd, "missing op");
        return;
    }
#if !RMB_BUILTIN_SPI
    /* Reject unsupported operations before ownership, GPIO, UART or staging. */
    if (strcmp(op->valuestring, "status") && strcmp(op->valuestring, "restart") &&
        strcmp(op->valuestring, "probe") && strcmp(op->valuestring, "flash") &&
        strcmp(op->valuestring, "ram")) {
        cJSON_Delete(request);
        failure(fd, "unsupported operation");
        return;
    }
#endif
    target_diag diagnostic = {.stage = "request",
                              .phase = "start",
                              .ack_byte = -1,
                              .rx_level = -1,
                              .boot0_level = -1,
                              .nrst_level = -1};
    rom_io io = {.ctx = &diagnostic,
                 .tx = target_tx,
                 .rx = target_rx,
                 .boot = target_boot,
                 .reset = target_reset,
                 .sleep_ms = target_sleep};
#if RMB_BUILTIN_SPI
    if (strcmp(op->valuestring, "spi-test") && (seen & 48)) {
        cJSON_Delete(request);
        failure(fd, "count/hz are spi-test only");
        return;
    }
    if ((seen & 64) && strcmp(op->valuestring, "spi-pad-sample")) {
        cJSON_Delete(request);
        failure(fd, "pull is spi-pad-sample only");
        return;
    }
    if (!strcmp(op->valuestring, "spi-pad-sample")) {
        cJSON *pull = cJSON_GetObjectItemCaseSensitive(request, "pull");
        unsigned mode = 0;
        bool parameters = (seen == 3 || seen == 67);
        if (seen & 64) {
            parameters = parameters && cJSON_IsString(pull);
            if (parameters && !strcmp(pull->valuestring, "up"))
                mode = 1;
            else if (parameters && !strcmp(pull->valuestring, "down"))
                mode = 2;
            else if (!parameters || strcmp(pull->valuestring, "none"))
                parameters = false;
        }
        cJSON_Delete(request);
        if (!parameters) {
            failure(fd, "spi-pad-sample accepts only optional pull none/up/down");
            return;
        }
        spi_pad_sample(fd, mode);
        return;
    }
#else
    /* Optional diagnostic parameters are not accepted by the UART service. */
    if (seen & 112) {
        cJSON_Delete(request);
        failure(fd, "unsupported request fields");
        return;
    }
#endif
    if (!strcmp(op->valuestring, "status")) {
        ESP_LOGI(TAG, "Operation received: status");
        service_status(fd);
        cJSON_Delete(request);
        return;
    }
#if RMB_BUILTIN_SPI
    if (!strcmp(op->valuestring, "spi-test")) {
        uint32_t count = 1, hz = 100000;
        bool parameters =
            !(seen & 12) &&
            (!(seen & 16) || number(cJSON_GetObjectItemCaseSensitive(request, "count"), &count)) &&
            (!(seen & 32) || number(cJSON_GetObjectItemCaseSensitive(request, "hz"), &hz));
        cJSON_Delete(request);
        if (!parameters || !spi_parameters_valid(count, hz)) {
            failure(fd, "spi-test requires integer count 1..100 and hz 100000..1000000");
            return;
        }
        spi_test(fd, count, hz);
        return;
    }
#endif
    if (!strcmp(op->valuestring, "restart")) {
        if (seen != 3) {
            cJSON_Delete(request);
            failure(fd, "restart takes no image or extra fields");
            return;
        }
        if (!control_acquire()) {
            cJSON_Delete(request);
            failure(fd, "UART busy");
            return;
        }
        *owned = true;
        ESP_LOGI(TAG, "Operation received: restart");
        int rc = supervisor_restart(&io);
        relay_target = rc ? "unknown" : "app";
        if (!rc && target_uart_mode(115200, false) != ESP_OK)
            rc = ROM_E_IO;
        ESP_LOGI(TAG, "Restart result=%d", rc);
        if (rc)
            failure(fd, "STM32 restart failed");
        else
            reply(fd, "{\"ok\":true}\n");
        cJSON_Delete(request);
        return;
    }
    if (!strcmp(op->valuestring, "probe")) {
        if (seen != 3) {
            cJSON_Delete(request);
            failure(fd, "probe takes no image or extra fields");
            return;
        }
        if (!control_acquire()) {
            cJSON_Delete(request);
            failure(fd, "UART busy");
            return;
        }
        *owned = true;
        cJSON_Delete(request);
        relay_target = "rom";
        diagnostic.probe = true;
        diagnostic.first_byte_us = -1;
        target_events(NULL); /* discard previous operation notifications, not RX */
        ESP_LOGI(TAG, "Explicit read-only probe: 115200 8E1, one reset/sync/Get/GetVersion/GetID; "
                      "NO erase/write/unprotect/Go/retry");
        int rc;
        rom_probe_result result = {0};
        if (target_raw_start(&diagnostic))
            rc = ROM_E_IO;
        else if (target_uart_mode(115200, true) != ESP_OK)
            rc = ROM_E_IO;
        else
            rc = supervisor_probe(&io, device, &result, target_trace, &diagnostic);
        if (rc) {
            ESP_LOGI(TAG, "Probe failure tail START: RX only for 1000ms at 115200; no "
                          "commands/retry; original error retained");
            uint8_t tail[32];
            unsigned total_tail = 0;
            int64_t until = esp_timer_get_time() + 1000000;
            while (esp_timer_get_time() < until && total_tail < 4096) {
                service_progress();
                int got = uart_read_bytes(UART_PORT, tail, sizeof tail, pdMS_TO_TICKS(20));
                if (got > 0) {
                    total_tail += (unsigned)got;
                    target_raw(&diagnostic, false, "failure-tail", tail, (size_t)got,
                               esp_timer_get_time());
                }
                target_events(&diagnostic);
            }
            ESP_LOGI(TAG, "Probe failure tail END bytes=%u limit=4096; original result unchanged",
                     total_tail);
        }
        target_events(&diagnostic);
        target_raw_stop(&diagnostic, "probe terminal (read-only)");
        esp_err_t restored = target_uart_mode(115200, false);
        ESP_LOGI(TAG,
                 "Probe result=%d commands=%u tx=%u rx=%u first_byte_task_observed_us=%lld (-1 "
                 "none); UART_FRAME_ERR=%u UART_PARITY_ERR=%u UART_FIFO_OVF=%u "
                 "UART_BUFFER_FULL=%u; counts may undercount if event queue fills",
                 rc, result.commands, diagnostic.tx_bytes, diagnostic.rx_bytes,
                 (long long)diagnostic.first_byte_us, diagnostic.frame_errors,
                 diagnostic.parity_errors, diagnostic.fifo_overflows, diagnostic.buffer_full);
        char report[1800];
        snprintf(report, sizeof report,
                 "{\"ok\":true,\"probe_ok\":%s,\"rc\":%d,\"error\":\"%s\",\"baud\":115200,"
                 "\"stage\":\"%s\",\"phase\":\"%s\",\"ack\":%d,\"commands\":%u,\"tx\":%u,\"rx\":%u,"
                 "\"version\":%u,\"device_id\":%u,\"pending_pre_sync\":%u,\"first_byte_task_"
                 "observed_us\":%lld,\"UART_FRAME_ERR\":%u,\"UART_PARITY_ERR\":%u,\"UART_FIFO_"
                 "OVF\":%u,\"UART_BUFFER_FULL\":%u,\"esp_local_rx\":%d,\"esp_local_boot0\":%d,"
                 "\"esp_local_nrst\":%d,\"gpio_scope\":\"ESP local pads only, not remote "
                 "STM32\",\"boot1\":\"%s\",\"baud_restored\":%s,\"note\":\"Flash unchanged; ROM "
                 "boot requested; no automatic restart/retry; event counts may undercount if queue "
                 "fills; GPIO -1 means not sampled; first-byte timing is task observed, not wire "
                 "timestamp\"}\n",
                 rc == 0 ? "true" : "false", rc, rom_strerror(rc), diagnostic.stage,
                 diagnostic.phase, diagnostic.ack_byte, result.commands, diagnostic.tx_bytes,
                 diagnostic.rx_bytes, result.version, result.device_id,
                 (unsigned)diagnostic.stale_count, (long long)diagnostic.first_byte_us,
                 diagnostic.frame_errors, diagnostic.parity_errors, diagnostic.fifo_overflows,
                 diagnostic.buffer_full, diagnostic.rx_level, diagnostic.boot0_level,
                 diagnostic.nrst_level,
                 CONFIG_ROM_SERVICE_BOOT1 < 0 ? "undriven/unmeasured"
                                              : "configured low; remote unmeasured",
                 restored == ESP_OK ? "true" : "false");
        /* Append explicit version diagnostics without changing existing JSON keys. */
        size_t report_len = strlen(report);
        if (report_len >= 2)
            snprintf(report + report_len - 2, sizeof(report) - report_len + 2,
                     ",\"get_version\":%u,\"option1\":%u,\"option2\":%u,\"version_queried\":%s,"
                     "\"version_mismatch\":%s,\"version_zero\":%s}\n",
                     result.get_version, result.option1, result.option2,
                     result.version_queried ? "true" : "false",
                     result.version_mismatch ? "true" : "false",
                     result.version_zero ? "true" : "false");
        reply(fd, report);
        return;
    }
    bool ram = !strcmp(op->valuestring, "ram");
    uint32_t size, crc;
    if ((!ram && strcmp(op->valuestring, "flash")) ||
        !number(cJSON_GetObjectItemCaseSensitive(request, "size"), &size) ||
        !number(cJSON_GetObjectItemCaseSensitive(request, "crc32"), &crc) || size < 8 ||
        size > (ram ? device->image_max : device->flash_size) || size > staging->size) {
        cJSON_Delete(request);
        failure(fd, "invalid operation, size or numeric crc32");
        return;
    }
    ESP_LOGI(TAG, "Operation received: %s size=%lu crc32=0x%08lx", ram ? "ram" : "flash",
             (unsigned long)size, (unsigned long)crc);
    cJSON_Delete(request);
    /* Only ESP staging is erased now. STM32 is untouched until full validation. */
    if (!control_acquire()) {
        failure(fd, "UART busy");
        return;
    }
    *owned = true;
    size_t erase_size = (size + 4095U) & ~4095U;
    if (erase_size > staging->size) {
        failure(fd, "image does not fit the staging partition");
        return;
    }
    for (size_t offset = 0; offset < erase_size; offset += 4096) {
        service_progress();
        if (esp_partition_erase_range(staging, offset, 4096) != ESP_OK) {
            failure(fd, "staging erase failed");
            return;
        }
        vTaskDelay(1);
    }
    if (!reply(fd, "{\"ok\":true,\"ready\":true}\n"))
        return;
    ESP_LOGI(TAG, "Staging upload start bytes=%lu", (unsigned long)size);
    int64_t upload_started = esp_timer_get_time();
    uint8_t block[1024];
    deadline = esp_timer_get_time() + UPLOAD_US;
    for (uint32_t offset = 0; offset < size;) {
        size_t n = size - offset;
        if (n > sizeof(block))
            n = sizeof(block);
        if (!socket_exact(fd, block, n, false, deadline)) {
            ESP_LOGE(TAG, "Staging upload stopped completed_bytes=%lu elapsed_ms=%lu",
                     (unsigned long)offset,
                     (unsigned long)((esp_timer_get_time() - upload_started) / 1000));
            failure(fd, "upload timeout or disconnect; target untouched");
            return;
        }
        if (esp_partition_write(staging, offset, block, n) != ESP_OK) {
            failure(fd, "staging write failed; target untouched");
            return;
        }
        offset += n;
    }
    ESP_LOGI(TAG, "Staging upload end bytes=%lu elapsed_ms=%lu", (unsigned long)size,
             (unsigned long)((esp_timer_get_time() - upload_started) / 1000));
    rom_source source = {.ctx = (void *)staging, .read = source_read};
    int rc = supervisor_validate(device, &source, size, crc, ram);
    if (rc) {
        ESP_LOGE(TAG, "Staging validation result=%d (%s); target untouched", rc, rom_strerror(rc));
        failure(fd, "image CRC, vector or range validation failed; target untouched");
        return;
    }
    ESP_LOGI(TAG, "Staging validated: %s size=%lu; target download start", ram ? "ram" : "flash",
             (unsigned long)size);
    relay_target = "unknown";
    diagnostic.first_byte_us = -1;
    target_events(NULL); /* discard prior operation notifications, not RX */
    if (target_uart_mode(115200, true) != ESP_OK)
        rc = ROM_E_IO;
    else if (target_raw_start(&diagnostic))
        rc = ROM_E_IO;
    else
        rc = supervisor_download(&io, device, &source, size, crc, ram, target_trace, &diagnostic);
    target_raw_stop(&diagnostic, "download terminal before any write");
    target_events(&diagnostic);
    ESP_LOGI(TAG,
             "Download post-sync UART errors: frame=%u parity=%u fifo=%u full=%u "
             "first_byte_task_observed_us=%lld (queue may undercount)",
             diagnostic.frame_errors, diagnostic.parity_errors, diagnostic.fifo_overflows,
             diagnostic.buffer_full, (long long)diagnostic.first_byte_us);
    ESP_LOGI(TAG, "Target download result=%d (%s) commands=%u tx=%u rx=%u", rc, rom_strerror(rc),
             diagnostic.command_index, diagnostic.tx_bytes, diagnostic.rx_bytes);
    if (rc) {
        char message[200];
        snprintf(message, sizeof(message),
                 "STM32 ROM download failed (%d) at %s/%s ACK=%d; no automatic retry; target may "
                 "be partial",
                 rc, diagnostic.stage, diagnostic.phase, diagnostic.ack_byte);
        failure(fd, message);
    } else {
        relay_target = "app";
        if (target_uart_mode(115200, false) != ESP_OK) {
            failure(fd, "APP UART restore failed");
            return;
        }
        reply(fd, "{\"ok\":true}\n");
        /* After a RAM image has been started, BOOT0 is still high. A later brown-out
         * or manual reset would then land in the ROM loader instead of the Flash
         * application. Drop it back to Flash boot; a running app is unaffected
         * because BOOT0 is only sampled at reset. */
        if (ram)
            (void)target_boot(NULL, 0);
    }
    ESP_LOGI(TAG, "Operation terminal rc=%d elapsed_ms=%lu", rc,
             (unsigned long)((esp_timer_get_time() - started) / 1000));
    /* Never reset on disconnect/failure: RAM Go must not be undone. */
}

static bool pin_allowed(int pin, bool output) {
    if (pin < 0 || pin == 2 || pin == 8 || pin == 9 || (pin >= 12 && pin <= 17))
        return false;
    return output ? GPIO_IS_VALID_OUTPUT_GPIO(pin) : GPIO_IS_VALID_GPIO(pin);
}
static bool configuration_valid(void) {
    const int pins[] = {CONFIG_ROM_SERVICE_TX, CONFIG_ROM_SERVICE_RX, CONFIG_ROM_SERVICE_BOOT0,
                        CONFIG_ROM_SERVICE_NRST, CONFIG_ROM_SERVICE_BOOT1};
    for (unsigned i = 0; i < 5; ++i) {
        if (i == 4 && pins[i] == -1)
            continue;
        if (!pin_allowed(pins[i], i != 1))
            return false;
        for (unsigned j = 0; j < i; ++j)
            if (pins[i] == pins[j])
                return false;
    }
    return true;
}

int module_business_start(const rmb_host *h) {
    host = h;
    root_lease = host->watchdog_open("stm-events", 5000);
    if (root_lease < 0)
        return -1;
    if (xTaskCreate(service_events, "stm-events", 2048, NULL, 3, NULL) != pdPASS)
        return -1;
#if defined(CONFIG_ROM_SERVICE_DEVICE_F103C8)
    device = &rom_device_f103c8;
#else
    device = &rom_device_f407zg;
#endif
    staging = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "staging");
    if (!staging)
        return -1;
    if (!configuration_valid())
        return -1;
    uart_owner = xSemaphoreCreateMutex();
    if (!uart_owner)
        return -1;
    ESP_ERROR_CHECK(esp_io_target_gpio_init());
    target_pad_report("startup BOOT0-low immediate", 0, 1);
    vTaskDelay(pdMS_TO_TICKS(10));
    target_pad_report("startup BOOT0-low settled", 0, 1);
    ESP_ERROR_CHECK(esp_io_uart_init(&uart_events, TARGET_RX_BUFFER_SIZE));
    ESP_LOGI(TAG, "Diagnostic A/B: UART1 RX GPIO%d internal weak pull-up enabled", BOARD_UART_RX);

    return 0;
}
void module_business_control(rmb_connection fd, void *context) {
    (void)context;
    control_lease = host->watchdog_open("stm-control", 10000);
    if (control_lease < 0)
        return;
    service_progress();
    bool owned = false;
    serve(fd, &owned);
    if (owned)
        control_release();
    (void)host->watchdog_close(control_lease);
    control_lease = -1;
}
void module_business_uart(rmb_connection fd, void *context) {
    (void)context;
    uart_lease = host->watchdog_open("stm-uart", 5000);
    if (uart_lease < 0)
        return;
    atomic_store_explicit(&uart_callback_task, xTaskGetCurrentTaskHandle(), memory_order_relaxed);
    service_io_progress();
    unsigned baud;
    bool history, framed = false;
    if (stream_auth_ex(fd, true, &baud, &history, &framed)) {
        if (framed)
            uart_framed_session(fd, baud);
        else
            uart_session(fd, baud);
    }
    (void)host->watchdog_close(uart_lease);
    uart_lease = -1;
    atomic_store_explicit(&uart_callback_task, NULL, memory_order_relaxed);
}
