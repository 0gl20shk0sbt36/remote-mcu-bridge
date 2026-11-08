/* ABI4 service: authenticated network admission with exact legacy request replay. */
#include "rmb/service.h"
#include "cJSON.h"
#include "platform.h"
#include <stdio.h>
#include <string.h>
#include <limits.h>
#ifndef SERVICE_VARIANT
#error SERVICE_VARIANT must be selected by manifest
#endif
#if SERVICE_VARIANT != 1 && SERVICE_VARIANT != 2
#error unsupported SERVICE_VARIANT
#endif
static const rmb_host *sdk;
static unsigned requests, echoes;
static int64_t started;
static int root_lease = -1;
static bool platform_ready;
void hello_root_heartbeat(void) {
    if (sdk && root_lease >= 0)
        (void)sdk->watchdog_feed(root_lease, 1);
}
/* Only the callback's stack carries its lease. The idle loop cannot feed it. */
static bool active_progress(int lease, uint32_t checkpoint) {
    return sdk->watchdog_feed(lease, checkpoint) >= 0;
}
static bool socket_exact(int fd, void *p, size_t n, bool writing, int64_t deadline, int lease) {
    /* fd is an opaque host handle despite this legacy local name. Nonblocking
     * chunks let the callback enforce its own deadline and task-owned lease. */
    size_t done = 0;
    while (done < n) {
        if (sdk->now_us() >= deadline || !active_progress(lease, 2))
            return false;
        size_t chunk = n - done;
        if (chunk > 128)
            chunk = 128;
        int result = writing ? sdk->net->write(fd, (const char *)p + done, chunk, true)
                             : sdk->net->read(fd, (char *)p + done, chunk, true);
        if (result == RMB_IO_AGAIN) {
            hello_platform_yield();
            continue;
        }
        if (result <= 0 || (size_t)result > chunk)
            return false;
        done += (size_t)result;
        if (!active_progress(lease, 3))
            return false;
        if (done < n)
            hello_platform_yield();
    }
    return sdk->now_us() < deadline;
}
static bool reply(int fd, const char *s, int lease) {
    return socket_exact(fd, (void *)s, strlen(s), true, sdk->now_us() + 1000000LL, lease);
}
static void failure(int fd, const char *message, int lease) {
    cJSON *o = cJSON_CreateObject();
    if (!o)
        return;
    cJSON_AddBoolToObject(o, "ok", false);
    cJSON_AddStringToObject(o, "error", message);
    char *s = cJSON_PrintUnformatted(o);
    if (s) {
        reply(fd, s, lease);
        reply(fd, "\n", lease);
        cJSON_free(s);
    }
    cJSON_Delete(o);
}
/* cJSON stores strings as C strings: reject raw and escaped NUL before parsing.
 * Skip escaped backslashes so literal text "\\u0000" remains representable. */
static bool no_nul_escape(const char *s, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (s[i] == '\\' && ++i < n) {
            if (s[i] == 'u' && i + 4 < n && !memcmp(s + i + 1, "0000", 4))
                return false;
        }
    return true;
}
static void serve(int fd, int lease) {
    char line[1025];
    size_t n = 0;
    int64_t deadline = sdk->now_us() + 10000000LL;
    for (;;) {
        char ch;
        if (!socket_exact(fd, &ch, 1, false, deadline, lease)) {
            failure(fd, "request timeout or closed", lease);
            return;
        }
        if (ch == '\n')
            break;
        if (!ch || n == 1024) {
            failure(fd, "invalid request length or NUL", lease);
            return;
        }
        line[n++] = ch;
    }
    line[n] = 0;
    if (!no_nul_escape(line, n)) {
        failure(fd, "NUL unsupported", lease);
        return;
    }
    const char *end = 0;
    if (!active_progress(lease, 4))
        return;
    cJSON *root = cJSON_ParseWithLengthOpts(line, n + 1, &end, true);
    if (!root || !end || end != line + n || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        failure(fd, "invalid JSON", lease);
        return;
    }
    cJSON *token = 0, *op = 0, *text = 0;
    bool valid = true;
    for (cJSON *p = root->child; p; p = p->next) {
        if (!active_progress(lease, 5)) {
            cJSON_Delete(root);
            return;
        }
        if (!p->string || !cJSON_IsString(p)) {
            valid = false;
            break;
        }
        cJSON **slot = 0;
        if (!strcmp(p->string, "token"))
            slot = &token;
        else if (!strcmp(p->string, "op"))
            slot = &op;
        else if (!strcmp(p->string, "text"))
            slot = &text;
        else {
            valid = false;
            break;
        }
        if (*slot) {
            valid = false;
            break;
        }
        *slot = p;
    }
    if (!valid || !token || !op || strlen(token->valuestring) > 128) {
        cJSON_Delete(root);
        failure(fd, "invalid schema", lease);
        return;
    }
    if (!sdk->net->authenticated(token->valuestring)) {
        cJSON_Delete(root);
        failure(fd, "authentication failed", lease);
        return;
    }
    bool echo = !strcmp(op->valuestring, "echo"), info = !strcmp(op->valuestring, "service-info");
    if ((!echo && !info) || (echo && (!text || strlen(text->valuestring) > 128)) ||
        (info && text)) {
        cJSON_Delete(root);
        failure(fd, "invalid operation or text", lease);
        return;
    }
    char copy[129] = {0};
    if (echo)
        memcpy(copy, text->valuestring, strlen(text->valuestring) + 1);
    /* Never retain pointers into parse tree or borrowed fd storage. */
    cJSON_Delete(root);
    if (echo && SERVICE_VARIANT == 2)
        for (size_t i = 0; copy[i]; i++)
            if (copy[i] >= 'a' && copy[i] <= 'z')
                copy[i] = (char)(copy[i] - 'a' + 'A');
    cJSON *value = cJSON_CreateString(copy);
    char *quoted = value ? cJSON_PrintUnformatted(value) : 0;
    if (!quoted) {
        cJSON_Delete(value);
        failure(fd, "allocation failed", lease);
        return;
    }
    if (requests < UINT_MAX)
        requests++;
    if (echo && echoes < UINT_MAX)
        echoes++;
    char output[1152];
    int used = snprintf(output, sizeof output,
                        "{\"ok\":true,\"service\":\"hello\",\"version\":\"%s\",\"op\":\"%s\","
                        "\"requests\":%u,\"echoes\":%u,\"uptime_ms\":%lld,\"state_reset\":\"on "
                        "module entry\",\"rom_supported\":false,\"text\":%s}\n",
                        SERVICE_VARIANT == 1 ? "A" : "B", echo ? "echo" : "service-info", requests,
                        echoes, (long long)((sdk->now_us() - started) / 1000), quoted);
    cJSON_free(quoted);
    cJSON_Delete(value);
    if (used < 0 || (size_t)used >= sizeof output) {
        failure(fd, "response overflow", lease);
        return;
    }
    (void)reply(fd, output, lease);
}
static void connection(rmb_connection fd, void *context) {
    (void)context;
    int lease = sdk->watchdog_open("hello.callback", 2000);
    if (lease < 0)
        return;
    serve(fd, lease);
    (void)sdk->watchdog_close(lease);
}
int module_entry(const rmb_host *host) {
    /* Check both version and table extent before dereferencing optional ABI
     * members; listeners become visible only if this whole entry succeeds. */
    if (!host || host->abi != RMB_SERVICE_ABI || host->size < sizeof *host || !host->net ||
        host->net->abi != RMB_SERVICE_ABI || host->net->size < sizeof *host->net || !host->now_us ||
        !host->net->listen || !host->net->read || !host->net->write || !host->net->authenticated ||
        !host->watchdog_open || !host->watchdog_feed || !host->watchdog_close)
        return -1;
    if (sdk && sdk != host)
        return -1;
    sdk = host;
    if (root_lease < 0) {
        root_lease = sdk->watchdog_open("hello.root", 2000);
        if (root_lease < 0)
            return -1;
    }
    if (!platform_ready) {
        if (hello_platform_start(hello_root_heartbeat) < 0)
            return -1;
        platform_ready = true;
    }
    requests = echoes = 0;
    started = sdk->now_us();
    /* Replay preserves the legacy one-line token+operation request: resident
     * admission checks auth first, then serve validates the full service schema. */
    return sdk->net->listen(3333, RMB_NET_AUTH_REPLAY, connection, 0);
}
const unsigned char module_base_binding[32] __attribute__((section(".module_base_id"), used)) = {1};
