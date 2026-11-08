#include "spi_diagnostic.h"
#include <stdio.h>
#include <string.h>
bool spi_parameters_valid(uint32_t count, uint32_t hz) {
    return count >= 1 && count <= 100 && hz >= 100000 && hz <= 1000000;
}
void spi_patterns(uint32_t seq, uint8_t tx[64], uint8_t expected[64]) {
    for (unsigned i = 0; i < 64; i++) {
        tx[i] = (uint8_t)(seq + i);
        expected[i] = (uint8_t)(seq ^ 0xa5U ^ i);
    }
}
int spi_parse_line(const char *line, uint32_t seq, bool done) {
    char expected[64];
    snprintf(expected, sizeof expected, done ? "SPI DONE %lu OK" : "SPI READY %lu",
             (unsigned long)seq);
    if (!strcmp(line, expected))
        return 1;
    snprintf(expected, sizeof expected, "SPI DONE %lu ERR", (unsigned long)seq);
    size_t n = strlen(expected);
    return !strncmp(line, expected, n) && (line[n] == '\0' || line[n] == ' ') ? -1 : 0;
}
static int wait_line(const spi_diagnostic_io *io, uint32_t seq, bool done, int64_t total) {
    int64_t end = io->now_us(io->ctx) + 1000000LL;
    if (end > total)
        end = total;
    while (io->now_us(io->ctx) < end) {
        char line[96];
        io->progress(io->ctx);
        int rc = io->read_line(io->ctx, line, sizeof line, end);
        if (rc < 0)
            return -2;
        if (!rc)
            return 0;
        int parsed = spi_parse_line(line, seq, done);
        if (parsed)
            return parsed;
    }
    return 0;
}
void spi_diagnostic_run(const spi_diagnostic_io *io, uint32_t count, uint32_t hz,
                        uint32_t *next_seq, spi_diagnostic_result *r) {
    memset(r, 0, sizeof *r);
    r->stage = "init";
    r->error = "none";
    int64_t started = io->now_us(io->ctx);
    int64_t deadline = started + 5000000LL + (int64_t)count * 2500000LL;
    r->deadline_ms = (deadline - started) / 1000;
    bool owned = false;
    if (!spi_parameters_valid(count, hz)) {
        r->error = "invalid parameters";
        goto finish_no_close;
    }
    if (io->busy(io->ctx)) {
        r->error = "previous SPI cleanup failed; reboot required";
        goto finish;
    }
    r->rc = io->open(io->ctx, hz, &r->error);
    if (r->rc)
        goto finish;
    for (unsigned n = 0; n < count; n++) {
        r->stage = "uart-acquire";
        if (io->now_us(io->ctx) >= deadline) {
            r->error = "total deadline";
            break;
        }
        if (!io->acquire(io->ctx)) {
            r->error = "UART busy";
            break;
        }
        owned = true;
        r->stage = "uart-mode";
        r->rc = io->prepare(io->ctx, &r->stage, &r->error);
        if (r->rc)
            break;
        r->last_seq = (*next_seq)++;
        if (!*next_seq)
            *next_seq = 1;
        r->stage = "uart-arm";
        if (io->arm(io->ctx, r->last_seq)) {
            r->error = "ARM TX";
            break;
        }
        r->stage = "uart-ready";
        int line = wait_line(io, r->last_seq, false, deadline);
        if (line != 1) {
            r->error = line == -1   ? "STM ERR before READY"
                       : line == -2 ? "UART read"
                                    : "READY timeout";
            break;
        }
        uint8_t tx[64], rx[64] = {0}, expected[64];
        spi_patterns(r->last_seq, tx, expected);
        r->stage = "spi-transfer";
        r->rc = io->transfer(io->ctx, tx, rx, &r->error);
        if (r->rc)
            break;
        for (unsigned i = 0; i < 64; i++)
            if (rx[i] != expected[i])
                r->rx_mismatch_bytes++;
        bool rx_ok = !memcmp(rx, expected, 64);
        if (!rx_ok && !r->rx_failure_captured) {
            static const char hex[] = "0123456789abcdef";
            r->rx_failure_captured = true;
            r->rx_failure_seq = r->last_seq;
            r->rx_min = 255;
            r->rx_max = 0;
            r->first_mismatch_index = 64;
            for (unsigned i = 0; i < 64; i++) {
                if (rx[i] < r->rx_min)
                    r->rx_min = rx[i];
                if (rx[i] > r->rx_max)
                    r->rx_max = rx[i];
                if (rx[i] != expected[i] && r->first_mismatch_index == 64) {
                    r->first_mismatch_index = i;
                    r->first_mismatch_actual = rx[i];
                    r->first_mismatch_expected = expected[i];
                }
                if (i < 16) {
                    r->rx_first16_hex[2 * i] = hex[rx[i] >> 4];
                    r->rx_first16_hex[2 * i + 1] = hex[rx[i] & 15];
                    r->expected_first16_hex[2 * i] = hex[expected[i] >> 4];
                    r->expected_first16_hex[2 * i + 1] = hex[expected[i] & 15];
                }
            }
            r->rx_constant = r->rx_min == r->rx_max;
        }
        if (rx_ok)
            r->esp_rx_verified++;
        r->stage = "uart-done";
        line = wait_line(io, r->last_seq, true, deadline);
        if (line == 1)
            r->stm_rx_verified++;
        if (line != 1) {
            r->error = line == -1   ? "STM RX verification ERR"
                       : line == -2 ? "UART read"
                                    : "DONE timeout";
            break;
        }
        r->stage = "spi-rx-verify";
        if (!rx_ok) {
            r->error = "ESP RX pattern mismatch";
            break;
        }
        r->completed++;
        io->release(io->ctx);
        owned = false;
    }
finish:
    if (owned)
        io->release(io->ctx);
    r->cleanup_rc = io->close(io->ctx);
    r->ok = r->completed == count && !r->cleanup_rc && !io->busy(io->ctx);
    if (r->ok)
        r->stage = "complete";
    else if (r->completed == count) {
        r->stage = "cleanup";
        r->error = "SPI cleanup";
    }
finish_no_close:
    r->elapsed_ms = (io->now_us(io->ctx) - started) / 1000;
}
