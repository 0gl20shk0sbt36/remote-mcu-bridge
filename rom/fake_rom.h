#ifndef FAKE_ROM_H
#define FAKE_ROM_H
#include "rom.h"
/* Heap/static allocate this large structure; no dynamic allocation inside fake. */
typedef struct fake_rom {
    uint8_t flash[1024 * 1024], ram[0x20000];
    uint8_t input[260], output[1024];
    size_t in_len, need, out_len, out_pos;
    uint32_t address;
    int state, command, boot0;
    unsigned boots, resets, go_count, erase_count, write_count, read_count, forbidden_count;
    uint16_t id;
    int classical, missing_read, timeout, bad_ack, corrupt_read, corrupt_padding;
    int fail_tx, fail_boot, fail_reset;
    size_t fragment; /* 0=unlimited, otherwise tx/rx max bytes per callback */
    uint32_t last_timeout;
    unsigned ack_count, bad_ack_at;        /* 1-based injected NACK stage; 0 disabled */
    uint32_t corrupt_offset;               /* UINT32_MAX disabled; relative to image base */
    unsigned sectors_erased;               /* bitmask of the first 32 erase units */
    unsigned units_erased;                 /* total erase units requested (both geometries) */
    const rom_device *dev;                 /* geometry/ID served by this fake part */
    int extended_erase;                    /* advertise 0x44 instead of 0x43 */
    uint8_t get_version, version_reply[3]; /* independently injectable wire bytes */
} fake_rom;
/* Defaults to rom_device_f407zg with extended erase advertised. */
void fake_rom_init(fake_rom *);
void fake_rom_init_device(fake_rom *, const rom_device *);
rom_io fake_rom_io(fake_rom *);
#endif
