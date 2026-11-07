#include "rmb/supervisor.h"

_Static_assert(sizeof(rmb_supervisor_record) == 20, "RTC record ABI");
_Static_assert(offsetof(rmb_supervisor_record, crc) == 16, "RTC CRC ABI");

uint32_t rmb_supervisor_crc32(const void *data, size_t size)
{
    const uint8_t *bytes = data;
    uint32_t crc = UINT32_MAX;
    while (size--) {
        crc ^= *bytes++;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & (0u - (crc & 1u)));
    }
    return ~crc;
}

bool rmb_supervisor_record_valid(const rmb_supervisor_record *record)
{
    return record && record->magic == RMB_SUPERVISOR_MAGIC &&
        record->crc == rmb_supervisor_crc32(record, offsetof(rmb_supervisor_record, crc));
}

void rmb_supervisor_record_write(rmb_supervisor_record *record,
                                 bool fault, bool active, uint32_t stage)
{
    record->magic = RMB_SUPERVISOR_MAGIC;
    record->fault = fault;
    record->active = active;
    record->stage = stage;
    record->crc = rmb_supervisor_crc32(record, offsetof(rmb_supervisor_record, crc));
}

bool rmb_supervisor_boot_fault(const rmb_supervisor_record *record, bool reset_fault)
{
    return reset_fault || (rmb_supervisor_record_valid(record) &&
                           (record->fault || record->active));
}

bool rmb_supervisor_deadline_expired(bool active, int64_t started_us,
                                     int64_t now_us, int64_t limit_us)
{
    return active && limit_us > 0 && started_us >= 0 && now_us >= started_us &&
        (uint64_t)now_us - (uint64_t)started_us > (uint64_t)limit_us;
}

void rmb_supervisor_entry_capture(rmb_supervisor_entry *entry, bool committed_at_boot)
{
    if (!entry->captured) {
        entry->allowed = committed_at_boot;
        entry->captured = true;
    }
}

bool rmb_supervisor_entry_can_start(const rmb_supervisor_entry *entry)
{
    return entry->captured && entry->allowed && !entry->submitted;
}

void rmb_supervisor_entry_submitted(rmb_supervisor_entry *entry)
{
    if (rmb_supervisor_entry_can_start(entry)) entry->submitted = true;
}
