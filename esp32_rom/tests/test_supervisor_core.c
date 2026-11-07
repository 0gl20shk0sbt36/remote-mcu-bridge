/* Native regression for portable resident policy; no ESP/hardware emulation. */
#include "rmb/supervisor.h"
#include "rom.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    assert(rmb_supervisor_crc32("123456789", 9) == UINT32_C(0xcbf43926));
    assert(rmb_supervisor_crc32(NULL, 0) == 0);
    uint8_t bytes[257];
    for (unsigned i = 0; i < sizeof bytes; ++i) bytes[i] = (uint8_t)i;
    for (unsigned i = 0; i <= sizeof bytes; ++i)
        assert(rmb_supervisor_crc32(bytes, i) == rom_crc32(bytes, i));
    rmb_supervisor_record record = {0};
    assert(!rmb_supervisor_record_valid(NULL));
    assert(!rmb_supervisor_record_valid(&record));
    assert(!rmb_supervisor_boot_fault(&record, false));
    assert(rmb_supervisor_boot_fault(&record, true));
    for (unsigned fault = 0; fault < 2; ++fault) {
        for (unsigned active = 0; active < 2; ++active) {
            rmb_supervisor_record_write(&record, fault, active, UINT32_MAX);
            assert(sizeof record == 20 && offsetof(rmb_supervisor_record, crc) == 16);
            assert(rmb_supervisor_record_valid(&record));
            assert(record.crc == rom_crc32((const uint8_t *)&record, 16));
            assert(record.stage == UINT32_MAX);
            assert(rmb_supervisor_boot_fault(&record, false) == (bool)(fault || active));
            for (unsigned bit = 0; bit < sizeof record * 8; ++bit) {
                rmb_supervisor_record corrupt = record;
                ((uint8_t *)&corrupt)[bit / 8] ^= (uint8_t)(1u << (bit % 8));
                assert(!rmb_supervisor_record_valid(&corrupt));
            }
        }
    }
    assert(!rmb_supervisor_deadline_expired(false, 0, 100, 1));
    assert(!rmb_supervisor_deadline_expired(true, 0, 100, 0));
    assert(!rmb_supervisor_deadline_expired(true, 0, 100, -1));
    assert(!rmb_supervisor_deadline_expired(true, 100, 99, 1));
    assert(!rmb_supervisor_deadline_expired(true, 100, 110, 10));
    assert(rmb_supervisor_deadline_expired(true, 100, 111, 10));
    assert(rmb_supervisor_deadline_expired(true, 0, INT64_MAX, INT64_MAX - 1));
    assert(!rmb_supervisor_deadline_expired(true, 0, INT64_MAX, INT64_MAX));
    assert(!rmb_supervisor_deadline_expired(true, -1, INT64_MAX, 1));
    rmb_supervisor_entry entry = {0};
    assert(!rmb_supervisor_entry_can_start(&entry));
    rmb_supervisor_entry_submitted(&entry);
    assert(!entry.submitted);
    rmb_supervisor_entry_capture(&entry, false);
    rmb_supervisor_entry_capture(&entry, true); /* Commit cannot activate update boot. */
    assert(!entry.allowed && !rmb_supervisor_entry_can_start(&entry));
    entry = (rmb_supervisor_entry){0}; /* Whole-chip reboot only. */
    rmb_supervisor_entry_capture(&entry, true);
    rmb_supervisor_entry_capture(&entry, false); /* Footer invalidation cannot recapture. */
    assert(entry.allowed && rmb_supervisor_entry_can_start(&entry));
    rmb_supervisor_entry_submitted(&entry);
    rmb_supervisor_entry_capture(&entry, true);
    assert(entry.submitted && !rmb_supervisor_entry_can_start(&entry));
    puts("PASS portable supervisor: legacy CRC/RTC ABI, corruption, boot fault, deadline edges, one-shot boot permission");
}
