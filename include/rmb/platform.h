/* SPDX-License-Identifier: MIT */
#ifndef BRIDGE_PLATFORM_H
#define BRIDGE_PLATFORM_H
#include <stddef.h>
#include <stdint.h>
/* Callbacks return a bridge_error, never SDK-specific values. ctx is caller owned.
 * NOR contract: successful erase leaves every byte 0xFF; program changes only
 * 1 bits to 0 bits (never 0 to 1). Failed operations may have changed any subset
 * of the addressed bytes, so callers must not assume rollback on an error.
 * program accepts aligned offset/length; core buffers fragmented transport writes
 * and pads the final granule with 0xFF. read/sha256 observe programmed storage,
 * not a stale mapping. Success means completion, not asynchronous submission.
 * Each storage is one slot, with no second persistent mode or A/B metadata. */
typedef enum {
    BRIDGE_OK = 0,
    BRIDGE_E_ARGUMENT = -1,
    BRIDGE_E_STATE = -2,
    BRIDGE_E_SIZE = -3,
    BRIDGE_E_IO = -4,
    BRIDGE_E_HASH = -5,
    BRIDGE_E_FORMAT = -6,
    BRIDGE_E_BINDING = -7,
    BRIDGE_E_NOT_FOUND = -8
} bridge_error;
typedef struct {
    /* Byte geometry, including footer; erase_unit is a multiple of write_unit. */
    size_t size, erase_unit, write_unit;
    int (*read)(void *ctx, size_t offset, void *data, size_t length);
    int (*erase)(void *ctx, size_t offset, size_t length);
    int (*program)(void *ctx, size_t offset, const void *data, size_t length);
    int (*sha256)(void *ctx, size_t offset, size_t length, uint8_t digest[32]);
    /* Admission only: format, exact base-image binding, expected image size.
     * MUST NOT execute uploaded code. */
    int (*check_image)(void *ctx, size_t expected_size);
    /* Optional mapping/cache synchronization; MUST NOT execute module entry. */
    int (*sync)(void *ctx);
} bridge_storage_ops;
/* Lifecycle services are separate from storage and from peripheral drivers. */
typedef struct {
    /* Task-context adapter operations; core neither starts nor owns these services. */
    uint64_t (*monotonic_us)(void *ctx);
    void (*watchdog_feed)(void *ctx);
    void (*reset)(void *ctx);
} bridge_lifecycle_ops;
/* Byte-stream contracts. Deadlines are absolute monotonic microseconds;
 * short positive transfers are normal; 0 is disconnect; negative is error.
 * Adapter retains socket/task ownership and authentication policy. */
typedef struct {
    int (*receive)(void *ctx, void *buffer, size_t capacity, uint64_t deadline_us);
    int (*send)(void *ctx, const void *buffer, size_t length, uint64_t deadline_us);
    void (*close)(void *ctx);
} bridge_stream_ops;
typedef struct {
    int (*listen)(void *ctx, uint16_t port);
    int (*accept)(void *ctx, void **stream_ctx, const bridge_stream_ops **stream);
} bridge_network_ops;
/* No unload callback: replaced instructions activate only after whole reset.
 * reserve must run before other dynamic mappings. admit cannot invoke entry.
 * enter is worker-only and can be called at most once per boot. */
typedef struct {
    int (*reserve)(void *ctx);
    int (*admit)(void *ctx, size_t stored_image_size);
    int (*enter)(void *ctx);
} bridge_module_load_ops;
#endif
