#ifndef MODULE_ABI_H
#define MODULE_ABI_H
/* Host networking uses borrowed slot/generation handles, never socket fds;
 * retaining one past its callback cannot authorize a reused connection. */
#include "rmb/service.h"
#include <stdbool.h>
#define MODULE_MAGIC 0x314d5242u
#define MODULE_TEXT_SIZE (0x100000u - MODULE_FOOTER_SIZE - sizeof(module_header))
#define MODULE_DRAM_SIZE 32768u
#define MODULE_FOOTER_SIZE 4096u
/* All on-flash fields little-endian uint32_t. image = header,text,data. */
typedef struct {
    uint32_t magic, abi, header_size, image_size, text_addr, text_size, data_addr, data_size,
        bss_size, entry;
} module_header;
/* Independently linked, not relocatable: both bases must match this resident.
 * XIP starts after the header in a 64KiB mapping; data+BSS share reserved DRAM.
 * The footer is commit metadata, not part of the executable image. */
static inline int module_header_valid(const module_header *h, uint32_t capacity, uint32_t text,
                                      uint32_t data) {
    return capacity >= MODULE_FOOTER_SIZE && h->magic == MODULE_MAGIC &&
           h->abi == MODULE_ABI_VERSION && h->header_size == sizeof *h &&
           h->image_size >= sizeof *h && h->image_size <= capacity - MODULE_FOOTER_SIZE &&
           h->text_addr == text && text >= 0x42000000u && text < 0x42800000u &&
           (text & 0xffffu) == sizeof *h && h->text_size <= 0x42800000u - text &&
           h->data_addr == data && h->text_size && h->text_size <= MODULE_TEXT_SIZE &&
           !(h->text_size & 3) && h->data_size <= MODULE_DRAM_SIZE &&
           h->bss_size <= MODULE_DRAM_SIZE - h->data_size &&
           h->text_size + h->data_size == h->image_size - sizeof *h && h->entry >= text &&
           h->entry - text < h->text_size && !(h->entry & 1);
}
#endif
