#include "module_runtime.h"
#include "rmb/update.h"
#include "generic_network.h"
#include <stdatomic.h>
#include <string.h>
#include "esp_partition.h"
#include "esp_attr.h"
#include "esp_app_desc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"
#include "mbedtls/sha256.h"
/* Only writable module state is reserved in SRAM. Instructions execute through
 * IDF's managed flash MMU mapping; SRAM PMS protections remain untouched. */
static const void *mapped_base;
static esp_partition_mmap_handle_t mapped_handle;
static uintptr_t reserved_base;
/* Linker-owned writable window: do not make SRAM executable or relax MEMPROT. */
uint8_t module_data_reserve[MODULE_DRAM_SIZE]
    __attribute__((section(".dram1.module_reserve"), aligned(16)));
static rmb_host host = {.abi = MODULE_ABI_VERSION, .size = sizeof(rmb_host)};
extern void module_host_configure(rmb_host *);
extern bool module_service_available(void);
extern bool module_watchdog_configured(void);
static bridge_update update;
static bridge_storage_ops storage;
static esp_err_t storage_prepare(const esp_partition_t *p);
static esp_err_t remap(void);
bool module_runtime_update_allowed(void) {
    return bridge_update_allowed(&update);
}
static atomic_bool admitted;
static bool attempted;
static module_header admitted_header;
static bool busy, installing, slot_invalid;
static const esp_partition_t *part(void) {
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x41, "business");
}
bool module_runtime_ready(void) {
    return atomic_load(&admitted) && !installing && !slot_invalid && module_service_available();
}
bool module_runtime_busy(void) {
    return busy;
}
const char *module_runtime_info(void) {
    return module_runtime_ready() ? "flash-xip-abi4-ready" : "module-unavailable";
}
uintptr_t module_runtime_text_address(void) {
    return reserved_base ? reserved_base + sizeof(module_header) : 0;
}
uintptr_t module_runtime_data_address(void) {
    return (uintptr_t)module_data_reserve;
}
/* Call before NVS/Wi-Fi and any other dynamic mappings, even with erased flash.
 * No assumed virtual address: the linker consumes the reported actual address.
 * Retain the whole 1MiB allocation during installation to reserve its VA. */
esp_err_t module_runtime_prepare(void) {
    if (mapped_base)
        return ESP_OK;
    const esp_partition_t *p = part();
    if (!p)
        return ESP_ERR_NOT_FOUND;
    if (p->size != 0x100000u || p->address != 0x220000u)
        return ESP_ERR_INVALID_SIZE;
    esp_err_t boot = storage_prepare(p);
    if (boot)
        return boot;
    const void *v = NULL;
    esp_partition_mmap_handle_t handle;
    esp_err_t e = esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_INST, &v, &handle);
    if (e)
        return e;
    if (reserved_base && (uintptr_t)v != reserved_base) {
        esp_partition_munmap(handle);
        return ESP_ERR_INVALID_STATE;
    }
    mapped_base = v;
    mapped_handle = handle;
    reserved_base = (uintptr_t)v;
    return ESP_OK;
}
/* Official unmap/remap after writes refreshes the cache/MMU view. The recorded
 * VA must survive; if another allocation occupied it, never execute the module. */
static esp_err_t remap(void) {
    if (mapped_base) {
        esp_partition_munmap(mapped_handle);
        mapped_base = NULL;
    }
    return module_runtime_prepare();
}
static esp_err_t sane(const esp_partition_t *p, module_header *h) {
    esp_err_t e = esp_partition_read(p, 0, h, sizeof *h);
    if (e)
        return e;
    if (!module_header_valid(h, p->size, (uint32_t)module_runtime_text_address(),
                             (uint32_t)module_data_reserve))
        return ESP_ERR_INVALID_ARG;
    /* The data prefix binds absolute references to this exact resident build. */
    uint8_t binding[32];
    if (h->data_size < sizeof binding)
        return ESP_ERR_INVALID_SIZE;
    e = esp_partition_read(p, sizeof *h + h->text_size, binding, sizeof binding);
    if (e)
        return e;
    if (memcmp(binding, esp_app_get_description()->app_elf_sha256, sizeof binding))
        return ESP_ERR_INVALID_VERSION;
    return ESP_OK;
}
/* Range/alignment check for callbacks, not admission or a sandbox boundary. */
bool module_runtime_code_pointer(const void *p) {
    uintptr_t v = (uintptr_t)p;
    return attempted && v >= admitted_header.text_addr &&
           v - admitted_header.text_addr < admitted_header.text_size && !(v & 1);
}
static esp_err_t load(const esp_partition_t *p) {
    module_header h;
    esp_err_t e = sane(p, &h);
    if (e)
        return e;
    e = esp_partition_read(p, sizeof h + h.text_size, module_data_reserve, h.data_size);
    if (e)
        return e;
    memset(module_data_reserve + h.data_size, 0, h.bss_size);
    __asm__ volatile("fence.i" ::: "memory");
    /* Listeners remain staged until entry succeeds with a live root lease. */
    admitted_header = h;
    module_host_configure(&host);
    rmb_network_init_begin();
    int result = ((module_entry_fn)(uintptr_t)h.entry)(&host);
    if (result || !module_watchdog_configured() || !rmb_network_init_commit()) {
        rmb_network_init_abort();
        return ESP_FAIL;
    }
    slot_invalid = false;
    atomic_store(&admitted, true);
    return ESP_OK;
}
/* ESP adapter owns partition discovery, SHA implementation and XIP admission.
 * Portable core owns footer, immutable boot latch and all transfer transitions. */
static int storage_read(void *ctx, size_t o, void *d, size_t n) {
    return esp_partition_read(ctx, o, d, n) ? BRIDGE_E_IO : 0;
}
static int storage_erase(void *ctx, size_t o, size_t n) {
    return esp_partition_erase_range(ctx, o, n) ? BRIDGE_E_IO : 0;
}
static int storage_program(void *ctx, size_t o, const void *d, size_t n) {
    return esp_partition_write(ctx, o, d, n) ? BRIDGE_E_IO : 0;
}
static int storage_check(void *ctx, size_t n) {
    module_header h;
    esp_err_t e = sane(ctx, &h);
    if (e)
        return e == ESP_ERR_INVALID_VERSION ? BRIDGE_E_BINDING : BRIDGE_E_FORMAT;
    return h.image_size == n ? BRIDGE_OK : BRIDGE_E_SIZE;
}
static int storage_hash(void *ctx, size_t o, size_t n, uint8_t digest[32]) {
    uint8_t b[512];
    mbedtls_sha256_context c;
    mbedtls_sha256_init(&c);
    int rc = mbedtls_sha256_starts(&c, 0);
    while (!rc && n) {
        size_t k = n > sizeof b ? sizeof b : n;
        if (esp_partition_read(ctx, o, b, k)) {
            mbedtls_sha256_free(&c);
            return BRIDGE_E_IO;
        }
        rc = mbedtls_sha256_update(&c, b, k);
        o += k;
        n -= k;
    }
    if (!rc)
        rc = mbedtls_sha256_finish(&c, digest);
    mbedtls_sha256_free(&c);
    return rc ? BRIDGE_E_HASH : BRIDGE_OK;
}
static int storage_sync(void *ctx) {
    (void)ctx;
    return remap() ? BRIDGE_E_IO : BRIDGE_OK;
}
static esp_err_t sdk_error(int e) {
    switch (e) {
    case BRIDGE_OK:
        return ESP_OK;
    case BRIDGE_E_ARGUMENT:
        return ESP_ERR_INVALID_ARG;
    case BRIDGE_E_STATE:
        return ESP_ERR_INVALID_STATE;
    case BRIDGE_E_SIZE:
        return ESP_ERR_INVALID_SIZE;
    case BRIDGE_E_HASH:
        return ESP_ERR_INVALID_CRC;
    case BRIDGE_E_BINDING:
        return ESP_ERR_INVALID_VERSION;
    case BRIDGE_E_FORMAT:
        return ESP_ERR_INVALID_ARG;
    default:
        return ESP_FAIL;
    }
}
static esp_err_t storage_prepare(const esp_partition_t *p) {
    if (!update.ops) {
        /* ESP flash accepts byte writes; use a 256-byte program granule for bounded
         * fragmented writes, padded with erased bytes outside the exact image. */
        storage = (bridge_storage_ops){p->size,      4096,          256,
                                       storage_read, storage_erase, storage_program,
                                       storage_hash, storage_check, storage_sync};
        int e = bridge_update_init(&update, &storage, (void *)p, MODULE_FOOTER_SIZE,
                                   sizeof(module_header));
        if (e)
            return sdk_error(e);
    }
    return sdk_error(bridge_update_boot(&update));
}
/* Consume the one-shot attempt even on failure: entry may leave tasks behind. */
esp_err_t module_runtime_init(void) {
    if (attempted || busy || installing)
        return ESP_ERR_INVALID_STATE;
    attempted = true;
    atomic_store(&admitted, false);
    esp_err_t e = module_runtime_prepare();
    if (e)
        return e;
    if (!bridge_execution_allowed(&update))
        return ESP_ERR_INVALID_STATE;
    e = sdk_error(bridge_footer_verify(&update, 0));
    return e ? e : load(part());
}
esp_err_t module_runtime_invalidate(void) {
    const esp_partition_t *p = part();
    if (!p)
        return ESP_ERR_NOT_FOUND;
    esp_err_t e = storage_prepare(p);
    if (e)
        return e;
    e = sdk_error(bridge_update_invalidate(&update));
    installing = false;
    if (!e)
        slot_invalid = true;
    return e;
}
esp_err_t module_runtime_install_begin(size_t n) {
    if (!module_runtime_update_allowed() || attempted || busy)
        return ESP_ERR_INVALID_STATE;
    esp_err_t e = module_runtime_prepare();
    if (e)
        return e;
    e = sdk_error(bridge_update_begin(&update, n));
    installing = update.installing;
    return e;
}
esp_err_t module_runtime_install_write(size_t o, const void *d, size_t n) {
    return sdk_error(bridge_update_write(&update, o, d, n));
}
esp_err_t module_runtime_install_finish(const uint8_t digest[32]) {
    int e = bridge_update_finish(&update, digest);
    installing = update.installing;
    return sdk_error(e);
}
