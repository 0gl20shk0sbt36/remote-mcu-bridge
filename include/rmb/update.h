/* SPDX-License-Identifier: MIT */
#ifndef BRIDGE_UPDATE_H
#define BRIDGE_UPDATE_H
#include <stdbool.h>
#include "rmb/platform.h"
#define BRIDGE_MAX_WRITE_UNIT 256u
/* Volatile state, caller serializes operations. No allocation or hidden globals. */
typedef struct {
    const bridge_storage_ops *ops;
    void *ctx;
    size_t footer_size, minimum_image, expected, written, programmed, pending;
    bool boot_latched, update_allowed, installing, failed, committed_this_boot;
    uint8_t buffer[BRIDGE_MAX_WRITE_UNIT];
} bridge_update;
/* All sizes/offsets are bytes; ops and ctx must outlive this state. Return
 * BRIDGE_OK on success, otherwise bridge_error (including adapter errors).
 * init checks geometry only; boot must succeed before permission queries. */
int bridge_update_init(bridge_update *, const bridge_storage_ops *, void *, size_t footer,
                       size_t minimum_image);
/* Capture footer permission once per reset: all-zero means committed/executable;
 * any other successfully read footer permits upload, never execution. */
int bridge_update_boot(bridge_update *);
bool bridge_update_allowed(const bridge_update *);
bool bridge_execution_allowed(const bridge_update *);
int bridge_footer_verify(bridge_update *, uint8_t value);
/* Footer-only. Does not revoke boot latch, unload code, or touch body/RAM. */
int bridge_update_invalidate(bridge_update *);
/* Erases the entire single-slot body, including unused tail. Only an upload-
 * permitted boot may begin, so executing code can never be erased underneath. */
int bridge_update_begin(bridge_update *, size_t image_size);
/* Strictly contiguous offsets starting at zero; data is copied before return.
 * Invalid writes or storage failures poison the transfer; begin starts afresh. */
int bridge_update_write(bridge_update *, size_t offset, const void *, size_t length);
/* Hash covers exactly image_size bytes, not final 0xff alignment padding.
 * Validate/hash/sync precede footer commit. Success requires reset to execute;
 * even a post-commit sync error must not authorize another upload this boot. */
int bridge_update_finish(bridge_update *, const uint8_t sha256[32]);
#endif
