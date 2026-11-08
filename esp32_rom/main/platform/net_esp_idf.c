/* ESP-IDF network bring-up and discovery for the resident port.
 *
 * A port for another platform replaces this file: associate/link the interface,
 * report the address, and answer discovery queries. The resident only needs
 * "network is usable" plus the address.
 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "esp_event.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "lwip/inet.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "rmb/port.h"

#define DISCOVER_QUERY "RMB_DISCOVER_V1"
#define LEGACY_QUERY "STM32ROM_DISCOVER_V1"
#define ANNOUNCE_PERIOD_US 30000000LL

static const char *TAG = "rmb-net";
static esp_netif_t *station;
static char announcement[160];

/* Implemented by the resident: tells it the interface is usable (or not). */
extern void rmb_resident_net_event(int connected);

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        RMB_LOGI(TAG, "station started; connecting (2.4 GHz only)");
        esp_err_t rc = esp_wifi_connect();
        if (rc != ESP_OK)
            RMB_LOGE(TAG, "connect: %s", esp_err_to_name(rc));
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
        RMB_LOGI(TAG, "associated; waiting for an address");
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = data;
        RMB_LOGW(TAG, "disconnected: reason=%u; reconnecting (no credentials logged)",
                 event ? (unsigned)event->reason : 0);
        rmb_resident_net_event(0);
        esp_err_t rc = esp_wifi_connect();
        if (rc != ESP_OK)
            RMB_LOGE(TAG, "reconnect: %s", esp_err_to_name(rc));
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        RMB_LOGI(TAG, "ready: IP=" IPSTR " gateway=" IPSTR, IP2STR(&event->ip_info.ip),
                 IP2STR(&event->ip_info.gw));
        RMB_LOGI(TAG, "LAN control TCP %d / discovery UDP %d", CONFIG_RMB_PORT,
                 CONFIG_RMB_DISCOVERY_PORT);
        rmb_resident_net_event(1);
    }
}

/* Discovery stays on the station subnet and never reaches target control. */
static void discovery_task(void *arg) {
    (void)arg;
    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (fd < 0) {
        RMB_LOGE(TAG, "discovery socket failed errno=%d", errno);
        rmb_port_task_end();
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
        RMB_LOGE(TAG, "discovery bind failed errno=%d", errno);
        close(fd);
        rmb_port_task_end();
        return;
    }
    int64_t next = 0;
    for (;;) {
        rmb_net_info info;
        if (!rmb_port_net_info(&info)) {
            rmb_port_delay_ticks(RMB_MS_TO_TICKS(1000));
            continue;
        }
        struct sockaddr_in peer;
        socklen_t peer_len = sizeof(peer);
        char query[64];
        int n = recvfrom(fd, query, sizeof(query), 0, (struct sockaddr *)&peer, &peer_len);
        bool asked = (n == (int)(sizeof(DISCOVER_QUERY) - 1) &&
                      !memcmp(query, DISCOVER_QUERY, sizeof(DISCOVER_QUERY) - 1)) ||
                     (n == (int)(sizeof(LEGACY_QUERY) - 1) &&
                      !memcmp(query, LEGACY_QUERY, sizeof(LEGACY_QUERY) - 1));
        struct in_addr address = {.s_addr = info.address};
        struct in_addr mask = {.s_addr = info.netmask};
        if (asked && (peer.sin_addr.s_addr & info.netmask) == (info.address & info.netmask))
            sendto(fd, announcement, strlen(announcement), 0, (struct sockaddr *)&peer, peer_len);
        (void)address;
        (void)mask;
        int64_t now = rmb_port_now_us();
        if (now >= next) {
            struct sockaddr_in broadcast = {.sin_family = AF_INET,
                                            .sin_port = htons(CONFIG_RMB_DISCOVERY_PORT),
                                            .sin_addr.s_addr = info.address | ~info.netmask};
            sendto(fd, announcement, strlen(announcement), 0, (struct sockaddr *)&broadcast,
                   sizeof(broadcast));
            next = now + ANNOUNCE_PERIOD_US;
        }
    }
}

int rmb_port_net_start(const char *ssid, const char *password) {
    esp_err_t rc = nvs_flash_init();
    if (rc == ESP_ERR_NVS_NO_FREE_PAGES || rc == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        RMB_LOGW(TAG, "NVS format incompatible/full; recreating NVS partition");
        if (nvs_flash_erase() != ESP_OK || nvs_flash_init() != ESP_OK)
            return -1;
    } else if (rc != ESP_OK) {
        return -1;
    }
    if (esp_netif_init() != ESP_OK || esp_event_loop_create_default() != ESP_OK)
        return -1;
    station = esp_netif_create_default_wifi_sta();
    if (!station)
        return -1;
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&init) != ESP_OK ||
        esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL) != ESP_OK ||
        esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL) != ESP_OK)
        return -1;
    wifi_config_t wifi = {0};
    memcpy(wifi.sta.ssid, ssid, strlen(ssid));
    memcpy(wifi.sta.password, password, strlen(password));
    if (esp_wifi_set_storage(WIFI_STORAGE_RAM) != ESP_OK ||
        esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK ||
        esp_wifi_set_config(WIFI_IF_STA, &wifi) != ESP_OK)
        return -1;
    uint8_t mac[6];
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK)
        return -1;
    snprintf(announcement, sizeof(announcement),
             "{\"service\":\"rmb-module\",\"id\":\"%02x%02x%02x%02x%02x%02x\",\"version\":\"4\","
             "\"log_port\":3335,\"management_port\":3337,\"port\":%d}",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], CONFIG_RMB_PORT);
    RMB_LOGW(TAG, "plaintext service: isolate the trusted station LAN; never expose publicly");
    if (esp_wifi_start() != ESP_OK)
        return -1;
    if (rmb_port_task_create("rom-discovery", discovery_task, NULL, 4096, 4, NULL))
        RMB_LOGE(TAG, "discovery task failed");
    return 0;
}

int rmb_port_net_info(rmb_net_info *info) {
    if (!info || !station)
        return 0;
    esp_netif_ip_info_t ip;
    if (esp_netif_get_ip_info(station, &ip) != ESP_OK || !ip.ip.addr)
        return 0;
    info->address = ip.ip.addr;
    info->netmask = ip.netmask.addr;
    info->gateway = ip.gw.addr;
    if (esp_read_mac(info->mac, ESP_MAC_WIFI_STA) != ESP_OK) {
        /* A missing MAC must not look like a usable address. */
        return 0;
    }
    return 1;
}
