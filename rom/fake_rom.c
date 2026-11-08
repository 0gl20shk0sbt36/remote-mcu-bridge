#include "fake_rom.h"
#include <string.h>
enum { SYNC, COMMAND, ADDRESS, WRITE, READ, ERASE };
static void put(fake_rom *f, uint8_t b) {
    if (f->out_len < sizeof f->output)
        f->output[f->out_len++] = b;
}
static void ok(fake_rom *f) {
    f->ack_count++;
    put(f, (f->bad_ack || f->ack_count == f->bad_ack_at) ? 0x1f : 0x79);
}
static void next(fake_rom *f, int state, size_t n) {
    f->state = state;
    f->need = n;
    f->in_len = 0;
}
/* Start offset of erase unit i, and its length, for the configured geometry. */
static uint32_t unit_start(const rom_device *d, unsigned i) {
    if (d->sector_ends)
        return i ? d->sector_ends[i - 1] : 0;
    return i * d->page_size;
}
static uint32_t unit_len(const rom_device *d, unsigned i) {
    if (d->sector_ends)
        return d->sector_ends[i] - unit_start(d, i);
    return d->page_size;
}
static unsigned unit_count(const rom_device *d) {
    return d->sector_ends ? d->sector_count : d->pages;
}
static uint8_t *memory(fake_rom *f, uint32_t a, size_t n) {
    const rom_device *d = f->dev;
    if (a >= d->flash_base && a - d->flash_base < d->flash_size &&
        n <= d->flash_size - (a - d->flash_base))
        return f->flash + (a - d->flash_base);
    if (a >= d->ram_base && a - d->ram_base < d->ram_size && n <= d->ram_size - (a - d->ram_base))
        return f->ram + (a - d->ram_base);
    return 0;
}
static void process(fake_rom *f) {
    uint8_t *b = f->input, x = 0;
    size_t n = f->in_len;
    for (size_t j = 0; j < n; j++)
        x ^= b[j];
    if (f->state == SYNC) {
        if (b[0] == 0x7f)
            ok(f);
        else
            put(f, 0x1f);
        next(f, COMMAND, 2);
        return;
    }
    if (f->state == COMMAND) {
        if (x != 0xff) {
            put(f, 0x1f);
            next(f, COMMAND, 2);
            return;
        }
        f->command = b[0];
        ok(f);
        switch (b[0]) {
        case 0: {
            uint8_t c[] = {0, 1, 2, 0x11, 0x21, 0x31, 0x43};
            c[6] = f->extended_erase ? 0x44 : 0x43;
            c[3] = f->missing_read ? 0x12 : 0x11;
            put(f, 7);
            put(f, f->get_version);
            for (size_t j = 0; j < 7; j++)
                put(f, c[j]);
            ok(f);
            break;
        }
        case 1:
            for (unsigned j = 0; j < 3; j++)
                put(f, f->version_reply[j]);
            ok(f);
            break;
        case 2:
            put(f, 1);
            put(f, (uint8_t)(f->id >> 8));
            put(f, (uint8_t)f->id);
            ok(f);
            break;
        case 0x11:
        case 0x21:
        case 0x31:
            next(f, ADDRESS, 5);
            return;
        case 0x43:
        case 0x44:
            next(f, ERASE, f->command == 0x44 ? 2 : 1);
            return;
        default:
            f->forbidden_count++;
            break;
        }
        next(f, COMMAND, 2);
        return;
    }
    if (f->state == ADDRESS) {
        f->address = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | b[3];
        if (x || !memory(f, f->address, 4)) {
            put(f, 0x1f);
            next(f, COMMAND, 2);
            return;
        }
        ok(f);
        if (f->command == 0x21) {
            f->go_count++;
            next(f, COMMAND, 2);
        } else
            next(f, f->command == 0x31 ? WRITE : READ, f->command == 0x31 ? 1 : 2);
        return;
    }
    if (f->state == WRITE) {
        if (n == 1) {
            f->need = (size_t)b[0] + 3;
            return;
        }
        size_t z = (size_t)b[0] + 1;
        uint8_t *p = memory(f, f->address, z);
        if (x || !p || (z & 3u) || (f->address & 3u))
            put(f, 0x1f);
        else {
            memcpy(p, b + 1, z);
            f->write_count++;
            ok(f);
        }
        next(f, COMMAND, 2);
        return;
    }
    if (f->state == READ) {
        size_t z = (size_t)b[0] + 1;
        uint8_t *p = memory(f, f->address, z);
        uint32_t base = f->address >= f->dev->image_base ? f->dev->image_base : f->dev->flash_base;
        if (x != 0xff || !p)
            put(f, 0x1f);
        else {
            ok(f);
            for (size_t j = 0; j < z; j++)
                put(f, p[j] ^ ((f->corrupt_read && j == 0) || (f->corrupt_padding && j == z - 1) ||
                                       (f->corrupt_offset != UINT32_MAX &&
                                        f->address - base + j == f->corrupt_offset)
                                   ? 1
                                   : 0));
            f->read_count++;
        }
        next(f, COMMAND, 2);
        return;
    }
    if (f->state == ERASE) {
        int ext = f->command == 0x44;
        size_t header = ext ? 2 : 1;
        unsigned units = unit_count(f->dev);
        if (n == header) {
            unsigned count = ext ? ((unsigned)b[0] * 256 + b[1]) + 1 : (unsigned)b[0] + 1;
            if (count > units) {
                f->forbidden_count++;
                put(f, 0x1f);
                next(f, COMMAND, 2);
                return;
            }
            f->need = header + count * (ext ? 2 : 1) + 1;
            return;
        }
        if (x)
            put(f, 0x1f);
        else {
            for (size_t j = header; j < n - 1; j += ext ? 2 : 1) {
                unsigned s = ext ? (unsigned)b[j] * 256 + b[j + 1] : b[j];
                if (s >= units) {
                    f->forbidden_count++;
                    continue;
                }
                memset(f->flash + unit_start(f->dev, s), 0xff, unit_len(f->dev, s));
                if (s < 32)
                    f->sectors_erased |= 1u << s;
                f->units_erased++;
            }
            f->erase_count++;
            ok(f);
        }
        next(f, COMMAND, 2);
    }
}
static int send(void *c, const uint8_t *p, size_t n) {
    fake_rom *f = c;
    if (f->fail_tx)
        return -1;
    if (f->fragment && n > f->fragment)
        n = f->fragment;
    for (size_t j = 0; j < n; j++) {
        if (f->in_len >= sizeof f->input)
            return -1;
        f->input[f->in_len++] = p[j];
        if (f->in_len == f->need)
            process(f);
    }
    return (int)n;
}
static int receive(void *c, uint8_t *p, size_t n, uint32_t t) {
    fake_rom *f = c;
    f->last_timeout = t;
    if (f->timeout)
        return 0;
    size_t z = f->out_len - f->out_pos;
    if (n > z)
        n = z;
    if (f->fragment && n > f->fragment)
        n = f->fragment;
    memcpy(p, f->output + f->out_pos, n);
    f->out_pos += n;
    if (f->out_pos == f->out_len)
        f->out_pos = f->out_len = 0;
    return (int)n;
}
static int boot(void *c, int system) {
    fake_rom *f = c;
    f->boots++;
    f->boot0 = system;
    return f->fail_boot ? -1 : 0;
}
static int reset(void *c) {
    fake_rom *f = c;
    f->resets++;
    next(f, SYNC, 1);
    f->out_len = f->out_pos = 0;
    return f->fail_reset ? -1 : 0;
}
static void sleep_ms(void *c, uint32_t ms) {
    (void)c;
    (void)ms;
}
void fake_rom_init_device(fake_rom *f, const rom_device *dev) {
    memset(f, 0, sizeof *f);
    memset(f->flash, 0xff, sizeof f->flash);
    memset(f->ram, 0, sizeof f->ram);
    f->dev = dev;
    f->id = dev->device_id;
    f->extended_erase = dev->erase_order[0] == 0x44;
    f->get_version = 0x31;
    f->version_reply[0] = 0x31;
    f->corrupt_offset = UINT32_MAX;
    next(f, SYNC, 1);
}
void fake_rom_init(fake_rom *f) {
    fake_rom_init_device(f, &rom_device_f407zg);
}
rom_io fake_rom_io(fake_rom *f) {
    rom_io i = {f, send, receive, boot, reset, sleep_ms};
    return i;
}
