/* Log ring and its TCP subscriber. Included by resident.c.
 *
 * Portable: sockets are the platform's BSD-style API (documented in rmb/port.h);
 * everything else goes through the port contract.
 */
#include <stdarg.h>
#include "stream_ring.h"

#define LOG_PORT 3335

static struct stream_ring log_ring;
static rmb_lock_t log_lock = RMB_LOCK_INIT;

/* Called by the platform logger for every formatted line. Interrupt-safe by
 * contract: no allocation, logging, UART, network or task wait. */
void rmb_log_capture(const char *bytes, size_t used) {
    if (!bytes || !used)
        return;
    char record[STREAM_RECORD_BYTES];
    if (used >= sizeof record) {
        static const char mark[] = " [log truncated]\n";
        used = sizeof record - 1;
        memcpy(record, bytes, used - (sizeof(mark) - 1));
        memcpy(record + used - (sizeof(mark) - 1), mark, sizeof(mark) - 1);
    } else {
        memcpy(record, bytes, used);
    }
    rmb_port_lock_enter(&log_lock);
    stream_push(&log_ring, record, used);
    rmb_port_lock_exit(&log_lock);
}

static bool stream_auth(int fd, bool *history) {
    char line[REQUEST_MAX + 1];
    size_t n = 0;
    int64_t deadline = rmb_port_now_us() + HEADER_US;
    while (n < REQUEST_MAX) {
        if (!socket_exact(fd, line + n, 1, false, deadline) || !line[n])
            return false;
        if (line[n++] == '\n')
            break;
    }
    if (!n || line[n - 1] != '\n' || !json_no_nul(line, n))
        return false;
    line[n] = 0;
    cJSON *r = cJSON_ParseWithLengthOpts(line, n + 1, NULL, true);
    unsigned seen = 0;
    bool valid = cJSON_IsObject(r);
    cJSON *it;
    cJSON_ArrayForEach(it, r) {
        unsigned bit = !strcmp(it->string, "token")     ? 1
                       : !strcmp(it->string, "op")      ? 2
                       : !strcmp(it->string, "history") ? 4
                                                        : 0;
        if (!bit || (seen & bit))
            valid = false;
        seen |= bit;
    }
    cJSON *token = cJSON_GetObjectItemCaseSensitive(r, "token"),
          *op = cJSON_GetObjectItemCaseSensitive(r, "op"),
          *h = cJSON_GetObjectItemCaseSensitive(r, "history");
    valid = valid && cJSON_IsString(token) && authenticated(token->valuestring) &&
            cJSON_IsString(op) && !strcmp(op->valuestring, "logs");
    *history = true;
    if (h) {
        valid = valid && cJSON_IsBool(h);
        *history = cJSON_IsTrue(h);
    }
    cJSON_Delete(r);
    return valid;
}

static bool stream_send(int fd, const void *bytes, size_t n) {
    return socket_exact(fd, (void *)bytes, n, true, rmb_port_now_us() + 1000000LL);
}

static void log_session(int fd, bool history) {
    uint64_t cursor;
    rmb_port_lock_enter(&log_lock);
    cursor = history ? (log_ring.next > STREAM_RECORDS ? log_ring.next - STREAM_RECORDS : 0)
                     : log_ring.next;
    rmb_port_lock_exit(&log_lock);
    if (!reply(fd, "{\"ok\":true}\n"))
        return;
    for (;;) {
        char bytes[STREAM_RECORD_BYTES];
        uint64_t lost;
        rmb_port_lock_enter(&log_lock);
        size_t n = stream_pop(&log_ring, &cursor, bytes, &lost);
        rmb_port_lock_exit(&log_lock);
        if (lost) {
            char notice[96];
            int count = snprintf(notice, sizeof notice, "[logs dropped %llu overwritten records]\n",
                                 (unsigned long long)lost);
            if (!stream_send(fd, notice, (size_t)count))
                return;
        }
        if (n && !stream_send(fd, bytes, n))
            return;
        /* Detect disconnect or unexpected subscriber input even during silence. */
        char extra;
        int got = recv(fd, &extra, 1, MSG_DONTWAIT);
        if (got == 0 || got > 0 ||
            (got < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR))
            return;
        if (!n)
            rmb_port_delay_ticks(RMB_MS_TO_TICKS(20));
    }
}

static void stream_server(void *arg) {
    (void)arg;
    int server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server < 0) {
        rmb_port_task_end();
        return;
    }
    int one = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in addr = {
        .sin_family = AF_INET, .sin_port = htons(LOG_PORT), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(server, (struct sockaddr *)&addr, sizeof addr) || listen(server, 1)) {
        close(server);
        rmb_port_task_end();
        return;
    }
    for (;;) {
        rmb_port_event_wait(network_events, NETWORK_READY, true, RMB_WAIT_FOREVER);
        int fd = accept(server, NULL, NULL);
        if (fd < 0) {
            rmb_port_delay_ticks(RMB_MS_TO_TICKS(100));
            continue;
        }
        struct timeval timeout = {.tv_sec = 1};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
        bool history;
        if (stream_auth(fd, &history))
            log_session(fd, history);
        shutdown(fd, SHUT_RDWR);
        close(fd);
    }
}
