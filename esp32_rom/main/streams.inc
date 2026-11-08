/* Included by service.c: uses its authenticated, bounded socket helpers. */
#include <stdarg.h>
#include "stream_ring.h"
#define LOG_PORT 3335
static struct stream_ring log_ring;
static portMUX_TYPE log_lock = portMUX_INITIALIZER_UNLOCKED;
static vprintf_like_t console_print;
static int log_tee(const char *format, va_list args) {
    va_list console_args, capture_args;
    va_copy(console_args, args);
    va_copy(capture_args, args);
    int result = console_print ? console_print(format, console_args) : 0;
    va_end(console_args);
    char bytes[STREAM_RECORD_BYTES];
    int n = vsnprintf(bytes, sizeof(bytes), format, capture_args);
    va_end(capture_args);
    if (n > 0) {
        size_t used = (size_t)n;
        if (used >= sizeof(bytes)) {
            static const char mark[] = " [log truncated]\n";
            used = sizeof(bytes) - 1;
            memcpy(bytes + used - (sizeof(mark) - 1), mark, sizeof(mark) - 1);
        }
        /* No allocation, logging, UART calls, network, or task wait in capture. */
        portENTER_CRITICAL(&log_lock);
        stream_push(&log_ring, bytes, used);
        portEXIT_CRITICAL(&log_lock);
    }
    return result;
}
static bool stream_auth(int fd, bool *history) {
    char line[REQUEST_MAX + 1];
    size_t n = 0;
    int64_t deadline = esp_timer_get_time() + HEADER_US;
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
    return socket_exact(fd, (void *)bytes, n, true, esp_timer_get_time() + 1000000LL);
}
static void log_session(int fd, bool history) {
    uint64_t cursor;
    portENTER_CRITICAL(&log_lock);
    cursor = history ? (log_ring.next > STREAM_RECORDS ? log_ring.next - STREAM_RECORDS : 0)
                     : log_ring.next;
    portEXIT_CRITICAL(&log_lock);
    if (!reply(fd, "{\"ok\":true}\n"))
        return;
    for (;;) {
        char bytes[STREAM_RECORD_BYTES];
        uint64_t lost;
        portENTER_CRITICAL(&log_lock);
        size_t n = stream_pop(&log_ring, &cursor, bytes, &lost);
        portEXIT_CRITICAL(&log_lock);
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
            vTaskDelay(pdMS_TO_TICKS(20));
    }
}
static void stream_server(void *arg) {
    (void)arg;
    int server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server < 0) {
        vTaskDelete(NULL);
        return;
    }
    int one = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in addr = {
        .sin_family = AF_INET, .sin_port = htons(LOG_PORT), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(server, (struct sockaddr *)&addr, sizeof addr) || listen(server, 1)) {
        close(server);
        vTaskDelete(NULL);
        return;
    }
    for (;;) {
        xEventGroupWaitBits(events, CONNECTED, pdFALSE, pdTRUE, portMAX_DELAY);
        int fd = accept(server, NULL, NULL);
        if (fd < 0) {
            vTaskDelay(pdMS_TO_TICKS(100));
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
