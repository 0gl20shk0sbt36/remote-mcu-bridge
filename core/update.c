/* SPDX-License-Identifier: MIT */
#include "rmb/update.h"
#include <string.h>
static int poison(bridge_update *u, int error) {
    u->failed = true;
    return error;
}
int bridge_update_init(bridge_update *u, const bridge_storage_ops *o, void *ctx, size_t footer,
                       size_t minimum) {
    if (!u || !o || !o->read || !o->erase || !o->program || !o->sha256 || !o->check_image)
        return BRIDGE_E_ARGUMENT;
    if (!o->erase_unit || !o->write_unit || o->write_unit > BRIDGE_MAX_WRITE_UNIT || !footer ||
        footer >= o->size || o->size % o->erase_unit || footer % o->erase_unit ||
        o->erase_unit % o->write_unit || !minimum || minimum > o->size - footer)
        return BRIDGE_E_SIZE;
    memset(u, 0, sizeof *u);
    u->ops = o;
    u->ctx = ctx;
    u->footer_size = footer;
    u->minimum_image = minimum;
    return BRIDGE_OK;
}
/* Keep transport status separate from a successfully observed mismatch: a
 * storage callback may itself return BRIDGE_E_STATE, which must fail closed. */
static int footer_matches(bridge_update *u, uint8_t value, bool *matches) {
    *matches = false;
    uint8_t b[256];
    size_t end = u->ops->size;
    for (size_t p = end - u->footer_size; p < end;) {
        size_t n = end - p;
        if (n > sizeof b)
            n = sizeof b;
        int e = u->ops->read(u->ctx, p, b, n);
        if (e)
            return e;
        for (size_t i = 0; i < n; i++)
            if (b[i] != value)
                return BRIDGE_OK;
        p += n;
    }
    *matches = true;
    return BRIDGE_OK;
}
int bridge_footer_verify(bridge_update *u, uint8_t value) {
    if (!u || !u->ops)
        return BRIDGE_E_ARGUMENT;
    bool matches;
    int e = footer_matches(u, value, &matches);
    return e ? e : matches ? BRIDGE_OK : BRIDGE_E_STATE;
}
int bridge_update_boot(bridge_update *u) {
    /* Do not re-read after invalidation: footer mutations affect the NEXT boot,
     * not the permission captured for currently mapped instructions. */
    if (!u || !u->ops)
        return BRIDGE_E_ARGUMENT;
    if (u->boot_latched)
        return BRIDGE_OK;
    bool matches;
    int e = footer_matches(u, 0, &matches);
    if (e)
        return e; /* Any callback failure is not update permission. */
    u->update_allowed = !matches;
    u->boot_latched = true;
    return BRIDGE_OK;
}
bool bridge_update_allowed(const bridge_update *u) {
    return u && u->boot_latched && u->update_allowed && !u->committed_this_boot;
}
bool bridge_execution_allowed(const bridge_update *u) {
    return u && u->boot_latched && !u->update_allowed && !u->installing;
}
int bridge_update_invalidate(bridge_update *u) {
    if (!u || !u->ops)
        return BRIDGE_E_ARGUMENT;
    /* Abort any pending transfer, but leave running code and execution latch alone. */
    u->installing = false;
    u->failed = true;
    int e = u->ops->erase(u->ctx, u->ops->size - u->footer_size, u->footer_size);
    return e ? e : bridge_footer_verify(u, 255);
}
int bridge_update_begin(bridge_update *u, size_t n) {
    if (!bridge_update_allowed(u) || u->installing)
        return BRIDGE_E_STATE;
    size_t capacity = u->ops->size - u->footer_size;
    if (n < u->minimum_image || n > capacity)
        return BRIDGE_E_SIZE;
    /* Padding to write_unit cannot cross the body boundary (geometry validated). */
    int e = bridge_update_invalidate(u);
    if (e)
        return e;
    u->expected = n;
    u->written = u->programmed = u->pending = 0;
    e = u->ops->erase(u->ctx, 0, capacity);
    if (e)
        return poison(u, e);
    u->failed = false;
    u->installing = true;
    return BRIDGE_OK;
}
int bridge_update_write(bridge_update *u, size_t offset, const void *data, size_t n) {
    if (!u || !u->installing || u->failed)
        return BRIDGE_E_STATE;
    if (offset != u->written || !data || !n || n > u->expected - u->written)
        return poison(u, BRIDGE_E_ARGUMENT);
    const uint8_t *p = data;
    size_t unit = u->ops->write_unit;
    while (n) {
        size_t k = unit - u->pending;
        if (k > n)
            k = n;
        memcpy(u->buffer + u->pending, p, k);
        u->pending += k;
        u->written += k;
        p += k;
        n -= k;
        if (u->pending == unit) {
            int e = u->ops->program(u->ctx, u->programmed, u->buffer, unit);
            if (e)
                return poison(u, e);
            u->programmed += unit;
            u->pending = 0;
        }
    }
    return BRIDGE_OK;
}
int bridge_update_finish(bridge_update *u, const uint8_t digest[32]) {
    if (!u || !u->installing || u->failed || u->written != u->expected || !digest)
        return BRIDGE_E_STATE;
    int e;
    if (u->pending) {
        memset(u->buffer + u->pending, 255, u->ops->write_unit - u->pending);
        e = u->ops->program(u->ctx, u->programmed, u->buffer, u->ops->write_unit);
        if (e)
            return poison(u, e);
        u->pending = 0;
    }
    e = u->ops->check_image(u->ctx, u->expected);
    if (e)
        return poison(u, e);
    uint8_t actual[32];
    e = u->ops->sha256(u->ctx, 0, u->expected, actual);
    if (e)
        return poison(u, e);
    if (memcmp(actual, digest, 32))
        return poison(u, BRIDGE_E_HASH);
    if (u->ops->sync && (e = u->ops->sync(u->ctx)))
        return poison(u, e);
    e = bridge_footer_verify(u, 255);
    if (e)
        return poison(u, e);
    /* Admission complete before the first 0 bit of the all-zero commit footer.
     * A torn footer stays uncommitted unless every footer byte reads as zero. */
    memset(u->buffer, 0, u->ops->write_unit);
    for (size_t p = u->ops->size - u->footer_size; p < u->ops->size; p += u->ops->write_unit) {
        e = u->ops->program(u->ctx, p, u->buffer, u->ops->write_unit);
        if (e)
            return poison(u, e);
    }
    e = bridge_footer_verify(u, 0);
    if (e)
        return poison(u, e);
    /* Durable commit exists now. Even sync failure must never permit another upload. */
    u->committed_this_boot = true;
    u->installing = false;
    if (u->ops->sync && (e = u->ops->sync(u->ctx)))
        return e;
    return BRIDGE_OK;
}
