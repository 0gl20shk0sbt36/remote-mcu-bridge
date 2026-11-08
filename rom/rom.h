#ifndef ROM_H
#define ROM_H
#include <stddef.h>
#include <stdint.h>
/* UART is configured by caller as 8 data bits, even parity, 1 stop bit.
 * tx/rx return transferred byte count, 0 timeout, negative error; short I/O OK.
 * boot sets BOOT0 (1=system memory); reset pulses NRST. Return 0 on success.
 * source must be immutable throughout validate/download, random-access readable.
 * Calls are synchronous task/host operations, not ISR-safe. Caller serializes
 * each target and keeps I/O/source contexts alive until return; none are owned.
 * rx timeout is per callback in milliseconds, not a whole-session deadline.
 */
typedef struct rom_io {
    void *ctx;
    int (*tx)(void *, const uint8_t *, size_t);
    int (*rx)(void *, uint8_t *, size_t, uint32_t timeout_ms);
    int (*boot)(void *, int system_memory);
    int (*reset)(void *);
    void (*sleep_ms)(void *, uint32_t);
} rom_io;
typedef struct rom_source {
    void *ctx;
    int (*read)(void *, uint32_t offset, uint8_t *, size_t); /* 0 success */
} rom_source;

/* Target description: everything the protocol layer needs about one part, so a
 * new device is a data change, not a code change.
 *
 * sector_ends : byte offsets (from flash_base) where each erase unit ends, for
 *               parts with non-uniform sectors (STM32F4). NULL => uniform pages.
 * erase_order : preferred erase commands; first one the ROM advertises wins
 *               (0x44 Extended Erase, 0x43 Erase).
 * image_base  : where RAM images are loaded; Flash mode uses flash_base instead.
 */
typedef struct rom_device {
    const char *name;
    uint32_t flash_base, flash_size;
    uint32_t page_size, pages;
    const uint32_t *sector_ends;
    unsigned sector_count;
    uint32_t ram_base, ram_size;
    uint32_t image_base, image_max;
    uint32_t vector_align;
    uint16_t device_id; /* GetID PID: family-level, not a SKU check */
    uint8_t erase_order[2];
} rom_device;

extern const rom_device rom_device_f407zg; /* 1 MiB: 4x16K + 64K + 7x128K sectors */
extern const rom_device rom_device_f103c8; /* 64 KiB: 1 KiB pages, 20 KiB SRAM */

enum {
    ROM_OK = 0,
    ROM_E_ARG = -1,
    ROM_E_SOURCE = -2,
    ROM_E_CRC = -3,
    ROM_E_VECTOR = -4,
    ROM_E_IO = -5,
    ROM_E_TIMEOUT = -6,
    ROM_E_ACK = -7,
    ROM_E_ID = -8,
    ROM_E_CAP = -9,
    ROM_E_VERIFY = -10,
    ROM_E_PROTOCOL = -11,
    ROM_E_GEOMETRY = -12
};
/* CRC-32/ISO-HDLC over original size only; wire padding is 0xff to 4 bytes. */
uint32_t rom_crc32(const uint8_t *data, size_t size);
int rom_validate(const rom_device *, const rom_source *, uint32_t size, uint32_t crc, int is_ram);
/* validate performs source-only size/vector/CRC checks and never touches target.
 * Download validates BEFORE BOOT0/reset, checks identity/capabilities before
 * writes, then verifies all padded bytes before Go (RAM) or reset (Flash).
 * is_ram is exactly 0 or 1; return ROM_OK or a negative ROM_E_* status.
 * Flash erases whole intersecting units: bytes past image end are not preserved.
 * Failure may leave partial programming and BOOT0 high; no automatic rollback. */
int rom_download(const rom_io *, const rom_device *, const rom_source *, uint32_t size,
                 uint32_t crc, int is_ram);
/* Optional synchronous diagnostics; no image bytes or secrets. byte=-1 when no
 * ACK byte was received. Existing rom_io layout and rom_download stay unchanged.
 * Metadata phases use address as value: bootloader-version, device-id,
 * covered-erase-units, capabilities-mask (Get,Version,ID,Read,Write,Go,43,44
 * in bits 0..7). version-options/version-zero/version-mismatch pack
 * Get version in bits 31..24 and GetVersion v[0..2] in bits 23..0.
 * Semantic failures have byte=-1, never a stale successful ACK.
 * Callback must not reenter or alter the I/O. */
typedef void (*rom_trace_fn)(void *ctx, const char *stage, const char *phase,
                             unsigned command_index, uint32_t address, int status, int byte);
int rom_download_traced(const rom_io *, const rom_device *, const rom_source *, uint32_t size,
                        uint32_t crc, int is_ram, rom_trace_fn, void *trace_ctx);
/* Read-only protocol probe: one ROM reset, sync, Get, GetVersion and GetID only.
 * Leaves target in ROM, never erases/writes/unprotects/Go. Result zero-initialized
 * even on failure; trace preserves exact failing ACK. rom_io is unchanged. */
typedef struct rom_probe_result {
    uint8_t version;
    uint16_t device_id;
    unsigned commands;
    /* Appended fields; rebuild consumers. version remains the prior Get version. */
    uint8_t get_version, option1, option2, version_queried, version_mismatch, version_zero;
} rom_probe_result;
int rom_probe(const rom_io *, const rom_device *, rom_probe_result *, rom_trace_fn, void *);
/* Restart already programmed Flash: BOOT0 low then reset, no ROM command. */
int rom_restart(const rom_io *);
const char *rom_strerror(int status);
#endif
