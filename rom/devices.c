/* Device table for the AN3155 host core. Adding a part is a data change:
 * fill in the geometry from its reference manual, pick the erase commands the
 * ROM advertises, and add the PID from its debug/ID register documentation. */
#include "rom.h"

/* RM0090 Table 5 (F405/407/415/417): 4x16K + 1x64K + 7x128K = 1 MiB.
 * AN2606 Table 73/75: ROM loader keeps 8 KiB (V3.x) or 12 KiB (V9.x) of SRAM,
 * so 16 KiB is reserved; VTOR needs 512-byte alignment (PM0214 §4.4.4). */
static const uint32_t f407_sectors[] = {0x4000,  0x8000,  0xC000,  0x10000, 0x20000, 0x40000,
                                        0x60000, 0x80000, 0xA0000, 0xC0000, 0xE0000, 0x100000};
const rom_device rom_device_f407zg = {.name = "STM32F407ZG",
                                      .flash_base = 0x08000000u,
                                      .flash_size = 0x100000u,
                                      .page_size = 0u,
                                      .pages = 0u,
                                      .sector_ends = f407_sectors,
                                      .sector_count = 12u,
                                      .ram_base = 0x20000000u,
                                      .ram_size = 0x20000u,
                                      .image_base = 0x20004000u,
                                      .image_max = 0x1C000u,
                                      .vector_align = 512u,
                                      .device_id = 0x413u,
                                      .erase_order = {0x44u, 0x43u}};

/* RM0008 Rev21: 64 KiB main Flash in uniform 1 KiB pages (medium density);
 * DS: 20 KiB SRAM. AN2606 Rev70 Table 45: the F10xxx ROM loader uses the first
 * 512 bytes of SRAM and NACKs Read/Write/Go below 0x20000200, so a 1 KiB
 * reserve keeps the image base 512-aligned for VTOR (PM0056 §4.3.4). */
const rom_device rom_device_f103c8 = {.name = "STM32F103C8",
                                      .flash_base = 0x08000000u,
                                      .flash_size = 0x10000u,
                                      .page_size = 0x400u,
                                      .pages = 64u,
                                      .sector_ends = 0,
                                      .sector_count = 0u,
                                      .ram_base = 0x20000000u,
                                      .ram_size = 0x5000u,
                                      .image_base = 0x20000400u,
                                      .image_max = 0x4C00u,
                                      .vector_align = 512u,
                                      .device_id = 0x410u,
                                      .erase_order = {0x43u, 0x44u}};
