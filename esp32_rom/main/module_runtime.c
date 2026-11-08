/* Module admission, executable mapping and single-slot installation.
 *
 * Portable: every storage and mapping operation goes through rmb/port.h. The
 * update state machine itself lives in core/update.c and is platform-free.
 */
#include "module_runtime.h"
#include "generic_network.h"
#include "rmb/port.h"
#include "rmb/update.h"
#include <stdatomic.h>
#include <string.h>

/* Writable module state only; instructions execute from the platform's
 * executable mapping. The section attribute is platform-supplied so this window
 * lands in ordinary RAM without relaxing memory protection. */
uint8_t module_data_reserve[MODULE_DRAM_SIZE] RMB_DRAM_RESERVE_SECTION;

static uintptr_t mapped_base;
static uintptr_t data_reserve;
static rmb_host host = {.abi = MODULE_ABI_VERSION, .size = sizeof(rmb_host)};
extern void module_host_configure(rmb_host *);
extern bool module_service_available(void);
extern bool module_watchdog_configured(void);
static bridge_update update;
static bridge_storage_ops storage;
static int storage_prepare(void);
static int remap(void);

static atomic_bool admitted;
static bool attempted;
static module_header admitted_header;
static bool busy, installing, slot_invalid;

bool module_runtime_ready(void) {
    return atomic_load(&admitted) && !installing && !slot_invalid && module_service_available();
}

bool module_runtime_busy(void) {
    return busy;
}

const char *module_runtime_info(void) {
    return module_runtime_ready() ? "flash-xip-abi4-ready" : "module-unavailable";
}

bool module_runtime_update_allowed(void) {
    return bridge_update_allowed(&update);
}

uintptr_t module_runtime_text_address(void) {
    return mapped_base ? mapped_base + sizeof(module_header) : 0;
}

uintptr_t module_runtime_data_address(void) {
    return data_reserve;
}

/* Call before other mappings at boot, even with an erased slot. No assumed
 * virtual address: the platform reports where the slot mapped, and modules are
 * linked against that reported address. */
int module_runtime_prepare(void) {
    if (mapped_base)
        return 0;
    uint32_t size = 0, footer = 0;
    if (rmb_port_slot_geometry(&size, &footer))
        return -1;
    if (size != MODULE_SLOT_SIZE || footer != MODULE_FOOTER_SIZE)
        return -1;
    if (storage_prepare())
        return -1;
    uint32_t text = 0;
    if (rmb_port_slot_map(&text))
        return -1;
    if (!text)
        return -1;
    if (mapped_base && mapped_base != text)
        return -1;
    mapped_base = text;
    data_reserve = (uintptr_t)module_data_reserve;
    return 0;
}

/* Remap after writes so the executable view observes the new bytes. The
 * recorded base must survive; if another allocation took it, never execute the
 * module. */
static int remap(void) {
    if (mapped_base) {
        rmb_port_slot_unmap();
        mapped_base = 0;
    }
    return module_runtime_prepare();
}

/* Header/size/binding admission: the module must fit the slot, declare the
 * addresses this boot actually mapped, and carry the running base hash. */
static int sane(module_header *h) {
    if (rmb_port_slot_read(0, h, sizeof *h))
        return -1;
    if (!module_header_valid(h, MODULE_SLOT_SIZE, (uint32_t)module_runtime_text_address(),
                             (uint32_t)data_reserve))
        return -1;
    /* The data prefix binds absolute references to this exact base build. */
    uint8_t binding[32], base[32];
    if (h->data_size < sizeof binding)
        return -1;
    if (rmb_port_slot_read(sizeof *h + h->text_size, binding, sizeof binding))
        return -1;
    if (rmb_port_base_sha256(base))
        return -1;
    if (memcmp(binding, base, sizeof binding))
        return -1;
    return 0;
}

/* Range/alignment check for callbacks, not admission or a sandbox boundary. */
bool module_runtime_code_pointer(const void *p) {
    uintptr_t v = (uintptr_t)p;
    return attempted && v >= admitted_header.text_addr &&
           v - admitted_header.text_addr < admitted_header.text_size && !(v & 1);
}

/* Copy data/BSS into the reserved DRAM window, then run entry exactly once on
 * the supervised worker. Listeners only become callable after success. */
static int load(void) {
    module_header h;
    if (sane(&h))
        return -1;
    if (rmb_port_slot_read(sizeof h + h.text_size, module_data_reserve, h.data_size))
        return -1;
    memset(module_data_reserve + h.data_size, 0, h.bss_size);
    /* Instruction-cache coherence after writing code bytes through the map. */
    __asm__ volatile("fence.i" ::: "memory");
    /* Listeners stay staged until entry succeeds with a live root lease. */
    admitted_header = h;
    module_host_configure(&host);
    rmb_network_init_begin();
    int result = ((module_entry_fn)(uintptr_t)h.entry)(&host);
    if (result || !module_watchdog_configured() || !rmb_network_init_commit()) {
        rmb_network_init_abort();
        return -1;
    }
    slot_invalid = false;
    atomic_store(&admitted, true);
    return 0;
}

/* The port owns storage access; the portable core owns the footer, the boot
 * latch and every transfer transition. */
static int storage_read(void *ctx, size_t o, void *d, size_t n) {
    (void)ctx;
    return rmb_port_slot_read((uint32_t)o, d, (uint32_t)n) ? BRIDGE_E_IO : 0;
}
static int storage_erase(void *ctx, size_t o, size_t n) {
    (void)ctx;
    return rmb_port_slot_erase((uint32_t)o, (uint32_t)n) ? BRIDGE_E_IO : 0;
}
static int storage_program(void *ctx, size_t o, const void *d, size_t n) {
    (void)ctx;
    return rmb_port_slot_write((uint32_t)o, d, (uint32_t)n) ? BRIDGE_E_IO : 0;
}
static int storage_check(void *ctx, size_t n) {
    (void)ctx;
    module_header h;
    if (sane(&h))
        return BRIDGE_E_FORMAT;
    return h.image_size == n ? BRIDGE_OK : BRIDGE_E_SIZE;
}
static int storage_hash(void *ctx, size_t o, size_t n, uint8_t digest[32]) {
    (void)ctx;
    return rmb_port_slot_sha256((uint32_t)o, (uint32_t)n, digest) ? BRIDGE_E_HASH : BRIDGE_OK;
}
static int storage_sync(void *ctx) {
    (void)ctx;
    return remap() ? BRIDGE_E_IO : BRIDGE_OK;
}

static int storage_prepare(void) {
    if (!update.ops) {
        /* Byte-programmable storage: bounded 256-byte granules, padded with
         * erased bytes outside the exact image. */
        storage = (bridge_storage_ops){MODULE_SLOT_SIZE, 4096,          256,
                                       storage_read,     storage_erase, storage_program,
                                       storage_hash,     storage_check, storage_sync};
        int e =
            bridge_update_init(&update, &storage, NULL, MODULE_FOOTER_SIZE, sizeof(module_header));
        if (e)
            return -1;
    }
    return bridge_update_boot(&update) ? -1 : 0;
}

/* Consume the one-shot attempt even on failure: entry may leave tasks behind. */
int module_runtime_init(void) {
    if (attempted || busy || installing)
        return -1;
    attempted = true;
    atomic_store(&admitted, false);
    if (module_runtime_prepare())
        return -1;
    if (!bridge_execution_allowed(&update))
        return -1;
    if (bridge_footer_verify(&update, 0))
        return -1;
    return load();
}

int module_runtime_invalidate(void) {
    uint32_t size = 0, footer = 0;
    if (rmb_port_slot_geometry(&size, &footer))
        return -1;
    if (storage_prepare())
        return -1;
    int e = bridge_update_invalidate(&update);
    installing = false;
    if (!e)
        slot_invalid = true;
    return e ? -1 : 0;
}

int module_runtime_install_begin(size_t n) {
    if (!module_runtime_update_allowed() || attempted || busy)
        return -1;
    if (module_runtime_prepare())
        return -1;
    int e = bridge_update_begin(&update, n);
    installing = update.installing;
    return e ? -1 : 0;
}

int module_runtime_install_write(size_t o, const void *d, size_t n) {
    return bridge_update_write(&update, o, d, n) ? -1 : 0;
}

int module_runtime_install_finish(const uint8_t digest[32]) {
    int e = bridge_update_finish(&update, digest);
    installing = update.installing;
    return e ? -1 : 0;
}
