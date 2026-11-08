#ifndef RMB_SUPERVISOR_H
#define RMB_SUPERVISOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RMB_SUPERVISOR_MAGIC UINT32_C(0x53555015)
/* Persistent representation is unchanged: five native uint32_t words. */
typedef struct {
    uint32_t magic, fault, active, stage, crc;
} rmb_supervisor_record;

uint32_t rmb_supervisor_crc32(const void *data, size_t size);
/* CRC checks integrity, not freshness or provenance; adapter owns persistence
 * and synchronization. An invalid record must not supply offender metadata. */
bool rmb_supervisor_record_valid(const rmb_supervisor_record *record);
void rmb_supervisor_record_write(rmb_supervisor_record *record, bool fault, bool active,
                                 uint32_t stage);
/* reset_fault is the adapter's panic/watchdog reset classification. */
bool rmb_supervisor_boot_fault(const rmb_supervisor_record *record, bool reset_fault);
/* Disabled/nonpositive limits never expire; equality is not expiration.
 * Times must be nonnegative monotonic microseconds. Clock rollback is ignored. */
bool rmb_supervisor_deadline_expired(bool active, int64_t started_us, int64_t now_us,
                                     int64_t limit_us);

/* Zero-initialize once per boot; never recapture after footer mutations.
 * Calls and submitted updates must be serialized by the platform adapter. */
typedef struct {
    bool captured, allowed, submitted;
} rmb_supervisor_entry;
void rmb_supervisor_entry_capture(rmb_supervisor_entry *entry, bool committed_at_boot);
bool rmb_supervisor_entry_can_start(const rmb_supervisor_entry *entry);
/* Call only after successful queue submission; failed sends do not consume it. */
void rmb_supervisor_entry_submitted(rmb_supervisor_entry *entry);

#ifdef __cplusplus
}
#endif
#endif
