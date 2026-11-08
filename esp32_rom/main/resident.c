/* Portable resident: authentication, management entry points and boot sequence.
 *
 * Nothing here includes a platform header. Sockets are the platform's BSD-style
 * API (see rmb/port.h); everything else goes through the port contract.
 * Trusted LAN only: plaintext authentication, no TLS.
 */
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include "cJSON.h"
#include "generic_network.h"
#include "module_runtime.h"
#include "rmb/port.h"

static const char *TAG = "rmb-base";

#define REQUEST_MAX 1024
#define HEADER_US 10000000LL
#define UPLOAD_US 180000000LL

/* Shared with streams.inc, supervisor.inc and the platform network layer. */
rmb_event_t network_events;
#define NETWORK_READY 1u

static void supervisor_progress(void);

void rmb_resident_net_event(int connected) {
    if (!network_events)
        return;
    if (connected)
        rmb_port_event_set(network_events, NETWORK_READY);
    else
        rmb_port_event_clear(network_events, NETWORK_READY);
}

/* Fixed secret-sized loop; no early exit on a mismatched byte. */
bool authenticated(const char *candidate) {
    const char *secret = RMB_CONFIG_TOKEN;
    size_t n = strlen(secret), m = candidate ? strlen(candidate) : 0;
    volatile unsigned difference = (unsigned)(m ^ n);
    for (size_t i = 0; i < n; ++i)
        difference |= (unsigned char)secret[i] ^ (i < m ? (unsigned char)candidate[i] : 0);
    return difference == 0;
}

bool socket_exact(int fd, void *buffer, size_t size, bool writing, int64_t deadline) {
    /* One absolute monotonic-us deadline spans retries and short transfers;
     * false does not roll back an already sent prefix. */
    unsigned char *p = buffer;
    while (size) {
        int64_t left = deadline - rmb_port_now_us();
        if (left <= 0)
            return false;
        struct timeval tv = {.tv_sec = left / 1000000, .tv_usec = left % 1000000};
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(fd, &fds);
        int r = select(fd + 1, writing ? NULL : &fds, writing ? &fds : NULL, NULL, &tv);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0 || rmb_port_now_us() >= deadline)
            return false;
        int n = writing ? send(fd, p, size, MSG_DONTWAIT) : recv(fd, p, size, MSG_DONTWAIT);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK))
            continue;
        if (n <= 0 || rmb_port_now_us() >= deadline)
            return false;
        p += n;
        size -= (size_t)n;
    }
    return rmb_port_now_us() < deadline;
}

bool reply(int fd, const char *text) {
    return socket_exact(fd, (void *)text, strlen(text), true, rmb_port_now_us() + HEADER_US);
}

void failure(int fd, const char *message) {
    RMB_LOGE(TAG, "Request failed: %s", message);
    cJSON *o = cJSON_CreateObject();
    if (!o)
        return;
    cJSON_AddBoolToObject(o, "ok", false);
    cJSON_AddStringToObject(o, "error", message);
    char *text = cJSON_PrintUnformatted(o);
    if (text) {
        reply(fd, text);
        reply(fd, "\n");
        cJSON_free(text);
    }
    cJSON_Delete(o);
}

bool number(cJSON *item, uint32_t *out) {
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) || item->valuedouble < 0 ||
        item->valuedouble > UINT32_MAX || floor(item->valuedouble) != item->valuedouble)
        return false;
    *out = (uint32_t)item->valuedouble;
    return true;
}

/* Reject a decoded NUL, but accept a literal escaped backslash followed by u0000. */
static bool json_no_nul(const char *s, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (!s[i])
            return false;
        if (s[i] == '\\' && i + 1 < n) {
            ++i;
            if (s[i] == 'u' && i + 4 < n && !memcmp(s + i + 1, "0000", 4))
                return false;
        }
    }
    return true;
}

#include "streams.inc"
#include "supervisor.inc"

bool module_service_available(void) {
    return !supervisor_fault;
}

#include "service_watchdog.inc"

void module_host_configure(rmb_host *h) {
    h->net = &rmb_network_api;
    h->now_us = rmb_port_now_us;
    h->progress = supervisor_progress;
    h->run = supervisor_run;
    h->watchdog_open = service_watchdog_open;
    h->watchdog_feed = service_watchdog_feed;
    h->watchdog_close = service_watchdog_close;
}

static bool configuration_valid(void) {
    size_t token = strlen(RMB_CONFIG_TOKEN);
    size_t ssid = strlen(RMB_CONFIG_SSID);
    size_t password = strlen(RMB_CONFIG_PASSWORD);
    return token >= 16 && token <= 128 && ssid > 0 && ssid <= 32 && password <= 64;
}

/* Entry point invoked by the platform once its runtime is ready. */
void rmb_resident_main(void) {
    RMB_LOGI(TAG, "RMB ABI4 generic resident loader");
    if (!configuration_valid()) {
        RMB_LOGE(TAG, "Invalid or unsafe configuration; service disabled");
        return;
    }
    /* Reserve the executable mapping before other mappings at boot. Prepare is
     * nonexecuting even for a blank or retained-quarantined slot. */
    if (module_runtime_prepare())
        RMB_LOGE(TAG, "module mapping prepare failed");
    if (rmb_port_event_create(&network_events)) {
        RMB_LOGE(TAG, "event group allocation failed");
        return;
    }
    if (rmb_port_net_start(RMB_CONFIG_SSID, RMB_CONFIG_PASSWORD)) {
        RMB_LOGE(TAG, "network start failed");
        return;
    }
    service_watchdog_boot();
    supervisor_init();

    /* Create every base service BEFORE management queues untrusted INIT. */
    if (rmb_port_task_create("wifi-logs", stream_server, NULL, 4096, 4, NULL))
        RMB_LOGE(TAG, "log task failed");
    if (rmb_port_task_create("module-management", supervisor_management, NULL, 8192, 5, NULL))
        RMB_LOGE(TAG, "management task failed");
}
