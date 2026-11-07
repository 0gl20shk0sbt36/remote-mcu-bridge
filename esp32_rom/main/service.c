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
#include "rom.h"

static const char *TAG = "stm32-rom";
#ifdef CONFIG_ROM_SERVICE_ENABLE
static void supervisor_progress(void);
#if defined(CONFIG_ROM_SERVICE_DEVICE_F103C8)
static const rom_device *const device = &rom_device_f103c8;
#else
static const rom_device *const device = &rom_device_f407zg;
#endif
#define CONNECTED BIT0
/* Never let diagnostics become target protocol bytes on a misconfigured build. */
#if defined(CONFIG_ESP_CONSOLE_UART_NUM) && CONFIG_ESP_CONSOLE_UART_NUM == 1
#error "ROM UART1 requires diagnostic console UART0 or USB, never UART1"
#endif
#define REQUEST_MAX 1024
#define HEADER_US 10000000LL
#define UPLOAD_US 180000000LL
static EventGroupHandle_t events;
static esp_netif_t *station;
static const esp_partition_t *staging;
static char announcement[160];

/* Fixed secret-sized loop; no early exit on secret byte mismatch. */
bool authenticated(const char *candidate) {
    const char *secret = CONFIG_ROM_SERVICE_TOKEN;
    size_t n = strlen(secret), m = candidate ? strlen(candidate) : 0;
    volatile unsigned difference = (unsigned)(m ^ n);
    for (size_t i = 0; i < n; ++i)
        difference |= (unsigned char)secret[i] ^ (i < m ? (unsigned char)candidate[i] : 0);
    return difference == 0;
}
bool socket_exact(int fd, void *buffer, size_t size, bool writing, int64_t deadline) {
    unsigned char *p = buffer;
    while (size) {
        int64_t left = deadline - esp_timer_get_time();
        if (left <= 0) return false;
        struct timeval tv = {.tv_sec = left / 1000000, .tv_usec = left % 1000000};
        fd_set fds;
        FD_ZERO(&fds); FD_SET(fd, &fds);
        int r = select(fd + 1, writing ? NULL : &fds, writing ? &fds : NULL, NULL, &tv);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return false;
        int n = writing ? send(fd, p, size, 0) : recv(fd, p, size, 0);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (n <= 0) return false;
        p += n; size -= (size_t)n;
    }
    return true;
}
bool reply(int fd, const char *text) {
    return socket_exact(fd, (void *)text, strlen(text), true, esp_timer_get_time() + HEADER_US);
}
void failure(int fd, const char *message) {
    ESP_LOGE(TAG, "Request failed: %s", message);
    cJSON *o = cJSON_CreateObject();
    if (!o) return;
    cJSON_AddBoolToObject(o, "ok", false);
    cJSON_AddStringToObject(o, "error", message);
    char *text = cJSON_PrintUnformatted(o);
    if (text) { reply(fd, text); reply(fd, "\n"); cJSON_free(text); }
    cJSON_Delete(o);
}
bool number(cJSON *item, uint32_t *out) {
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) || item->valuedouble < 0 ||
        item->valuedouble > UINT32_MAX || floor(item->valuedouble) != item->valuedouble) return false;
    *out = (uint32_t)item->valuedouble;
    return true;
}

#include "module_runtime.h"
static bool control_acquire(void) { return !module_runtime_ready() || module_runtime_dispatch(3,NULL)==0; }
static void control_release(void) { if(module_runtime_ready()) (void)module_runtime_dispatch(4,NULL); }
#include "streams.inc"
#include "supervisor.inc"
static bool business_available(void) { return !supervisor_fault && module_runtime_ready(); }
void module_host_configure(module_host *h) { h->progress=supervisor_progress;h->status=supervisor_status;h->available=business_available;h->validate=supervisor_validate;h->download=supervisor_download;h->probe=supervisor_probe;h->restart=supervisor_restart;h->device=device;h->staging=staging; }
static void serve(int fd,bool *owned) {
 (void)owned;
 if(module_runtime_ready()) (void)module_runtime_dispatch(1,&fd);
 else failure(fd,"business module unavailable; use management TCP 3337");
}
static bool configuration_valid(void) {
 size_t n=strlen(CONFIG_ROM_SERVICE_TOKEN);
 return n>=16 && n<=128 && strlen(CONFIG_ROM_SERVICE_WIFI_SSID)>0 && strlen(CONFIG_ROM_SERVICE_WIFI_SSID)<=32 && strlen(CONFIG_ROM_SERVICE_WIFI_PASSWORD)<=64;
}
static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "Wi-Fi station started; connecting (2.4 GHz only)");
        esp_err_t rc = esp_wifi_connect();
        if (rc != ESP_OK) ESP_LOGE(TAG, "wifi_connect: %s", esp_err_to_name(rc));
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
        ESP_LOGI(TAG, "Wi-Fi associated; waiting for DHCP");
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = data;
        ESP_LOGW(TAG, "Wi-Fi disconnected: reason=%u; reconnecting (no credentials logged)",
                 event ? (unsigned)event->reason : 0);
        xEventGroupClearBits(events, CONNECTED);
        esp_err_t rc = esp_wifi_connect();
        if (rc != ESP_OK) ESP_LOGE(TAG, "wifi reconnect: %s", esp_err_to_name(rc));
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        ESP_LOGI(TAG, "Wi-Fi ready: IP=" IPSTR " gateway=" IPSTR,
                 IP2STR(&event->ip_info.ip), IP2STR(&event->ip_info.gw));
        ESP_LOGI(TAG, "LAN control TCP %d / discovery UDP %d", CONFIG_ROM_SERVICE_PORT,
                 CONFIG_ROM_SERVICE_DISCOVERY_PORT);
        xEventGroupSetBits(events, CONNECTED);
    }
}
static void discovery(void *arg) {
    (void)arg;
    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (fd < 0) { ESP_LOGE(TAG,"Discovery socket failed errno=%d",errno); vTaskDelete(NULL); return; }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &one, sizeof(one));
    struct timeval timeout = {.tv_sec = 1};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = htons(CONFIG_ROM_SERVICE_DISCOVERY_PORT), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(fd, (struct sockaddr *)&local, sizeof(local)) < 0) { ESP_LOGE(TAG,"Discovery bind failed errno=%d",errno); close(fd); vTaskDelete(NULL); return; }
    int64_t next = 0;
    for (;;) {
        if (!(xEventGroupGetBits(events) & CONNECTED)) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
        esp_netif_ip_info_t ip;
        if (esp_netif_get_ip_info(station, &ip) != ESP_OK) continue;
        struct sockaddr_in peer;
        socklen_t peer_len = sizeof(peer);
        char query[64];
        int n = recvfrom(fd, query, sizeof(query), 0, (struct sockaddr *)&peer, &peer_len);
        /* Only reply to station-subnet peers; UDP can never invoke target control. */
        if (n == (int)(sizeof("STM32ROM_DISCOVER_V1") - 1) && !memcmp(query, "STM32ROM_DISCOVER_V1", sizeof("STM32ROM_DISCOVER_V1") - 1) &&
            (peer.sin_addr.s_addr & ip.netmask.addr) == (ip.ip.addr & ip.netmask.addr))
            sendto(fd, announcement, strlen(announcement), 0, (struct sockaddr *)&peer, peer_len);
        int64_t now = esp_timer_get_time();
        if (now >= next) {
            struct sockaddr_in broadcast = {.sin_family = AF_INET, .sin_port = htons(CONFIG_ROM_SERVICE_DISCOVERY_PORT),
                .sin_addr.s_addr = ip.ip.addr | ~ip.netmask.addr};
            sendto(fd, announcement, strlen(announcement), 0, (struct sockaddr *)&broadcast, sizeof(broadcast));
            next = now + 30000000LL;
        }
    }
}
static void control(void *arg) {
    (void)arg;
    int server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server < 0) { ESP_LOGE(TAG,"Control socket failed errno=%d",errno); vTaskDelete(NULL); return; }
    int one = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons(CONFIG_ROM_SERVICE_PORT), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(server, (struct sockaddr *)&addr, sizeof(addr)) || listen(server, 1)) {
        ESP_LOGE(TAG,"Control bind/listen failed errno=%d",errno);
        close(server); vTaskDelete(NULL); return;
    }
    ESP_LOGI(TAG,"Control listener ready TCP=%d",CONFIG_ROM_SERVICE_PORT);
    for (;;) {
        xEventGroupWaitBits(events, CONNECTED, pdFALSE, pdTRUE, portMAX_DELAY);
        int fd = accept(server, NULL, NULL);
        if (fd < 0) { ESP_LOGW(TAG,"Control accept failed errno=%d",errno); vTaskDelay(pdMS_TO_TICKS(100)); continue; }
        ESP_LOGI(TAG,"Control connection accepted");
        struct timeval timeout = {.tv_sec = 2};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        int64_t began=esp_timer_get_time();
        bool owned = false;
        serve(fd,&owned);
        if (owned) control_release();
        shutdown(fd, SHUT_RDWR); close(fd);
        ESP_LOGI(TAG,"Control connection closed elapsed_ms=%lu",(unsigned long)((esp_timer_get_time()-began)/1000));
    }
}
#endif

void app_main(void) {
    ESP_LOGI(TAG, "STM32 ROM bridge: v17 resident Supervisor + replaceable Flash XIP business module; WiFi logs + preemptible persistent framed APP UART; FULL UART1 pre-write hex; console 115200 baud");
#ifndef CONFIG_ROM_SERVICE_ENABLE
    ESP_LOGW(TAG, "Disabled by default; configure wiring and secret before enabling");
#else
    ESP_LOGI(TAG, "Target=%s; TX=%d RX=%d BOOT0=%d NRST=%d", device->name, CONFIG_ROM_SERVICE_TX, CONFIG_ROM_SERVICE_RX, CONFIG_ROM_SERVICE_BOOT0, CONFIG_ROM_SERVICE_NRST);
    console_print = esp_log_set_vprintf(log_tee);
    if (!configuration_valid()) { ESP_LOGE(TAG, "Invalid or unsafe configuration; service disabled"); return; }
    /* Reserve official XIP VA before NVS/Wi-Fi consume dynamic mappings.
     * Prepare is nonexecuting even for a blank or RTC-quarantined slot. */
    esp_err_t module_prepare_rc=module_runtime_prepare();
    if(module_prepare_rc!=ESP_OK) ESP_LOGE(TAG,"module XIP prepare failed: %d",(int)module_prepare_rc);
    staging = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "staging");
    if (!staging || staging->size < device->flash_size) { ESP_LOGE(TAG, "Staging partition smaller than the %s Flash", device->name); return; }
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
    if (!station || !events) { ESP_LOGE(TAG, "network allocation failed"); return; }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    wifi_config_t wifi = {0};
    memcpy(wifi.sta.ssid, CONFIG_ROM_SERVICE_WIFI_SSID, strlen(CONFIG_ROM_SERVICE_WIFI_SSID));
    memcpy(wifi.sta.password, CONFIG_ROM_SERVICE_WIFI_PASSWORD, strlen(CONFIG_ROM_SERVICE_WIFI_PASSWORD));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    uint8_t mac[6]; ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    snprintf(announcement, sizeof(announcement), "{\"service\":\"stm32-rom-uart\",\"id\":\"%02x%02x%02x%02x%02x%02x\",\"version\":\"1\",\"log_port\":3335,\"uart_port\":3336,\"port\":%d}",
        mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], CONFIG_ROM_SERVICE_PORT);
    ESP_LOGW(TAG, "Plaintext service: isolate trusted station LAN; never expose publicly");
    ESP_ERROR_CHECK(esp_wifi_start());
    supervisor_init();

    if (xTaskCreate(control, "rom-control", 8192, NULL, 5, NULL) != pdPASS) ESP_LOGE(TAG, "control task failed");
    if (xTaskCreate(discovery, "rom-discovery", 4096, NULL, 4, NULL) != pdPASS) ESP_LOGE(TAG, "discovery task failed");
    if (xTaskCreate(stream_server,"wifi-logs",4096,NULL,4,NULL) != pdPASS) ESP_LOGE(TAG,"log task failed");
    if (xTaskCreate(stream_server,"app-uart",8192,(void *)(uintptr_t)1,5,NULL) != pdPASS) ESP_LOGE(TAG,"UART task failed");
    /* Create every base service BEFORE management queues untrusted INIT. */
    if (xTaskCreate(supervisor_management,"module-management",8192,NULL,5,NULL) != pdPASS) ESP_LOGE(TAG,"management task failed");
#endif
}
