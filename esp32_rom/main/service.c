/* Trusted LAN only: plaintext authentication, no TLS. ESP-IDF 5.x / ESP32-C3. */
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <unistd.h>
#include "sdkconfig.h"
#include "cJSON.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "lwip/inet.h"
#include "nvs_flash.h"
#include "module_runtime.h"
#include "generic_network.h"
#include "esp_system.h"
#include "esp_task_wdt.h"

static const char *TAG = "rmb-base";
#ifdef CONFIG_RMB_ENABLE
static void supervisor_progress(void);
#define CONNECTED BIT0
#define REQUEST_MAX 1024
#define HEADER_US 10000000LL
#define UPLOAD_US 180000000LL
static EventGroupHandle_t events;
static esp_netif_t *station;
static char announcement[160];

/* Fixed secret-sized loop; no early exit on secret byte mismatch. */
bool authenticated(const char *candidate) {
    const char *secret = CONFIG_RMB_TOKEN;
    size_t n = strlen(secret), m = candidate ? strlen(candidate) : 0;
    volatile unsigned difference = (unsigned)(m ^ n);
    for (size_t i = 0; i < n; ++i)
        difference |= (unsigned char)secret[i] ^ (i < m ? (unsigned char)candidate[i] : 0);
    return difference == 0;
}
bool socket_exact(int fd, void *buffer, size_t size, bool writing, int64_t deadline) {
    /* Resident-only raw descriptor helper. One absolute monotonic-us deadline
     * spans retries/short transfers; false does not roll back a sent prefix. */
    unsigned char *p = buffer;
    while (size) {
        int64_t left = deadline - esp_timer_get_time();
        if (left <= 0)
            return false;
        struct timeval tv = {.tv_sec = left / 1000000, .tv_usec = left % 1000000};
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(fd, &fds);
        int r = select(fd + 1, writing ? NULL : &fds, writing ? &fds : NULL, NULL, &tv);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0 || esp_timer_get_time() >= deadline)
            return false;
        int n = writing ? send(fd, p, size, MSG_DONTWAIT) : recv(fd, p, size, MSG_DONTWAIT);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK))
            continue;
        if (n <= 0 || esp_timer_get_time() >= deadline)
            return false;
        p += n;
        size -= (size_t)n;
    }
    return esp_timer_get_time() < deadline;
}
bool reply(int fd, const char *text) {
    return socket_exact(fd, (void *)text, strlen(text), true, esp_timer_get_time() + HEADER_US);
}
void failure(int fd, const char *message) {
    ESP_LOGE(TAG, "Request failed: %s", message);
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

#include "module_runtime.h"
/* Reject decoded NUL, but accept a literal escaped backslash followed by u0000. */
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
    h->now_us = esp_timer_get_time;
    h->progress = supervisor_progress;
    h->run = supervisor_run;
    h->watchdog_open = service_watchdog_open;
    h->watchdog_feed = service_watchdog_feed;
    h->watchdog_close = service_watchdog_close;
}
static bool configuration_valid(void) {
    size_t n = strlen(CONFIG_RMB_TOKEN);
    return n >= 16 && n <= 128 && strlen(CONFIG_RMB_WIFI_SSID) > 0 &&
           strlen(CONFIG_RMB_WIFI_SSID) <= 32 && strlen(CONFIG_RMB_WIFI_PASSWORD) <= 64;
}
static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "Wi-Fi station started; connecting (2.4 GHz only)");
        esp_err_t rc = esp_wifi_connect();
        if (rc != ESP_OK)
            ESP_LOGE(TAG, "wifi_connect: %s", esp_err_to_name(rc));
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
        ESP_LOGI(TAG, "Wi-Fi associated; waiting for DHCP");
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = data;
        ESP_LOGW(TAG, "Wi-Fi disconnected: reason=%u; reconnecting (no credentials logged)",
                 event ? (unsigned)event->reason : 0);
        xEventGroupClearBits(events, CONNECTED);
        esp_err_t rc = esp_wifi_connect();
        if (rc != ESP_OK)
            ESP_LOGE(TAG, "wifi reconnect: %s", esp_err_to_name(rc));
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        ESP_LOGI(TAG, "Wi-Fi ready: IP=" IPSTR " gateway=" IPSTR, IP2STR(&event->ip_info.ip),
                 IP2STR(&event->ip_info.gw));
        ESP_LOGI(TAG, "LAN control TCP %d / discovery UDP %d", CONFIG_RMB_PORT,
                 CONFIG_RMB_DISCOVERY_PORT);
        xEventGroupSetBits(events, CONNECTED);
    }
}
static void discovery(void *arg) {
    (void)arg;
    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (fd < 0) {
        ESP_LOGE(TAG, "Discovery socket failed errno=%d", errno);
        vTaskDelete(NULL);
        return;
    }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &one, sizeof(one));
    struct timeval timeout = {.tv_sec = 1};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_in local = {.sin_family = AF_INET,
                                .sin_port = htons(CONFIG_RMB_DISCOVERY_PORT),
                                .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(fd, (struct sockaddr *)&local, sizeof(local)) < 0) {
        ESP_LOGE(TAG, "Discovery bind failed errno=%d", errno);
        close(fd);
        vTaskDelete(NULL);
        return;
    }
    int64_t next = 0;
    for (;;) {
        if (!(xEventGroupGetBits(events) & CONNECTED)) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        esp_netif_ip_info_t ip;
        if (esp_netif_get_ip_info(station, &ip) != ESP_OK)
            continue;
        struct sockaddr_in peer;
        socklen_t peer_len = sizeof(peer);
        char query[64];
        int n = recvfrom(fd, query, sizeof(query), 0, (struct sockaddr *)&peer, &peer_len);
        /* Only reply to station-subnet peers; UDP can never invoke target control. */
        if (((n == (int)(sizeof("RMB_DISCOVER_V1") - 1) &&
              !memcmp(query, "RMB_DISCOVER_V1", sizeof("RMB_DISCOVER_V1") - 1)) ||
             (n == (int)(sizeof("STM32ROM_DISCOVER_V1") - 1) &&
              !memcmp(query, "STM32ROM_DISCOVER_V1", sizeof("STM32ROM_DISCOVER_V1") - 1))) &&
            (peer.sin_addr.s_addr & ip.netmask.addr) == (ip.ip.addr & ip.netmask.addr))
            sendto(fd, announcement, strlen(announcement), 0, (struct sockaddr *)&peer, peer_len);
        int64_t now = esp_timer_get_time();
        if (now >= next) {
            struct sockaddr_in broadcast = {.sin_family = AF_INET,
                                            .sin_port = htons(CONFIG_RMB_DISCOVERY_PORT),
                                            .sin_addr.s_addr = ip.ip.addr | ~ip.netmask.addr};
            sendto(fd, announcement, strlen(announcement), 0, (struct sockaddr *)&broadcast,
                   sizeof(broadcast));
            next = now + 30000000LL;
        }
    }
}
#else
bool authenticated(const char *candidate) {
    (void)candidate;
    return false;
}
bool socket_exact(int fd, void *b, size_t n, bool w, int64_t d) {
    (void)fd;
    (void)b;
    (void)n;
    (void)w;
    (void)d;
    return false;
}
bool module_service_available(void) {
    return false;
}
bool module_watchdog_configured(void) {
    return false;
}
void module_host_configure(rmb_host *h) {
    (void)h;
}
#endif

void app_main(void) {
    /* Resident services survive module rejection/quarantine. Only management
     * may queue INIT after base logging, watchdogs and boot evidence are ready. */
    ESP_LOGI(TAG, "RMB ABI4 generic resident loader");
#ifndef CONFIG_RMB_ENABLE
    ESP_LOGW(TAG, "Disabled by default; configure wiring and secret before enabling");
#else
    console_print = esp_log_set_vprintf(log_tee);
    if (!configuration_valid()) {
        ESP_LOGE(TAG, "Invalid or unsafe configuration; service disabled");
        return;
    }
    /* Reserve official XIP VA before NVS/Wi-Fi consume dynamic mappings.
     * Prepare is nonexecuting even for a blank or RTC-quarantined slot. */
    esp_err_t module_prepare_rc = module_runtime_prepare();
    if (module_prepare_rc != ESP_OK)
        ESP_LOGE(TAG, "module XIP prepare failed: %d", (int)module_prepare_rc);
    esp_err_t nvs_rc = nvs_flash_init();
    if (nvs_rc == ESP_ERR_NVS_NO_FREE_PAGES || nvs_rc == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS format incompatible/full; recreating NVS partition");
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_rc = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_rc);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    station = esp_netif_create_default_wifi_sta();
    events = xEventGroupCreate();
    if (!station || !events) {
        ESP_LOGE(TAG, "network allocation failed");
        return;
    }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    wifi_config_t wifi = {0};
    memcpy(wifi.sta.ssid, CONFIG_RMB_WIFI_SSID, strlen(CONFIG_RMB_WIFI_SSID));
    memcpy(wifi.sta.password, CONFIG_RMB_WIFI_PASSWORD, strlen(CONFIG_RMB_WIFI_PASSWORD));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    snprintf(announcement, sizeof(announcement),
             "{\"service\":\"rmb-module\",\"id\":\"%02x%02x%02x%02x%02x%02x\",\"version\":\"4\","
             "\"log_port\":3335,\"management_port\":3337,\"port\":%d}",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], CONFIG_RMB_PORT);
    ESP_LOGW(TAG, "Plaintext service: isolate trusted station LAN; never expose publicly");
    ESP_ERROR_CHECK(esp_wifi_start());
    service_watchdog_boot();
    supervisor_init();

    if (xTaskCreate(discovery, "rom-discovery", 4096, NULL, 4, NULL) != pdPASS)
        ESP_LOGE(TAG, "discovery task failed");
    if (xTaskCreate(stream_server, "wifi-logs", 4096, NULL, 4, NULL) != pdPASS)
        ESP_LOGE(TAG, "log task failed");
    /* Create every base service BEFORE management queues untrusted INIT. */
    if (xTaskCreate(supervisor_management, "module-management", 8192, NULL, 5, NULL) != pdPASS)
        ESP_LOGE(TAG, "management task failed");
#endif
}
