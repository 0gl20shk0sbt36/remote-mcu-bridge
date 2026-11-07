#ifndef MODULE_ABI_H
#define MODULE_ABI_H
#include "rom.h"
#include <stdbool.h>
#define MODULE_ABI_VERSION 3u
#define MODULE_MAGIC 0x314d5242u
#define MODULE_TEXT_SIZE (0x100000u-MODULE_FOOTER_SIZE-sizeof(module_header))
#define MODULE_DRAM_SIZE 32768u
#define MODULE_FOOTER_SIZE 4096u
/* All on-flash fields little-endian uint32_t. image = header,text,data. */
typedef struct { uint32_t magic,abi,header_size,image_size,text_addr,text_size,data_addr,data_size,bss_size,entry; } module_header;
typedef struct { uint32_t abi,size;
 void (*progress)(void); void (*status)(int); bool (*available)(void);
 int (*validate)(const rom_device *,const rom_source *,uint32_t,uint32_t,int);
 int (*download)(const rom_io *,const rom_device *,const rom_source *,uint32_t,uint32_t,int,rom_trace_fn,void *);
 int (*probe)(const rom_io *,const rom_device *,rom_probe_result *,rom_trace_fn,void *);
 int (*restart)(const rom_io *);
 const rom_device *device; const void *staging;
} module_host;
typedef struct { uint32_t abi,size;
 int (*validate)(const rom_device *,const rom_source *,uint32_t,uint32_t,int);
 int (*download)(const rom_io *,const rom_device *,const rom_source *,uint32_t,uint32_t,int,rom_trace_fn,void *);
 int (*probe)(const rom_io *,const rom_device *,rom_probe_result *,rom_trace_fn,void *);
 int (*restart)(const rom_io *);
 int (*dispatch)(uint32_t,void *);
} module_ops;
typedef const module_ops *(*module_entry_fn)(const module_host *);
static inline int module_header_valid(const module_header *h,uint32_t capacity,uint32_t text,uint32_t data) {
 return capacity>=MODULE_FOOTER_SIZE && h->magic==MODULE_MAGIC && h->abi==MODULE_ABI_VERSION && h->header_size==sizeof *h && h->image_size>=sizeof *h && h->image_size<=capacity-MODULE_FOOTER_SIZE && h->text_addr==text && text>=0x42000000u && text<0x42800000u && (text&0xffffu)==sizeof *h && h->text_size<=0x42800000u-text && h->data_addr==data && h->text_size && h->text_size<=MODULE_TEXT_SIZE && !(h->text_size&3) && h->data_size<=MODULE_DRAM_SIZE && h->bss_size<=MODULE_DRAM_SIZE-h->data_size && h->text_size+h->data_size==h->image_size-sizeof *h && h->entry>=text && h->entry-text<h->text_size && !(h->entry&1);
}
#endif
