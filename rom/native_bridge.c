/* Host socket harness bridge: real AN3155 core + UART ROM model. */
#include "fake_rom.h"
#include <string.h>
static fake_rom target;
static const unsigned char *image;
static size_t length;
static const rom_device *device = &rom_device_f407zg;
static int source_read(void *ctx,uint32_t offset,uint8_t *out,size_t n) {
 (void)ctx;if(offset>length||n>length-offset)return -1;
 memcpy(out,image+offset,n);return 0;
}
/* which: 0 = STM32F407ZG, 1 = STM32F103C8 */
void bridge_init_device(int which) {
 device = which == 1 ? &rom_device_f103c8 : &rom_device_f407zg;
 fake_rom_init_device(&target, device);
 target.fragment = 3;
}
void bridge_init(void) { bridge_init_device(0); }
int bridge_download(const unsigned char *data,unsigned n,unsigned crc,int ram){
 rom_io io=fake_rom_io(&target);rom_source src={0,source_read};
 image=data;length=n;return rom_download(&io,device,&src,n,crc,ram);
}
int bridge_restart(void){rom_io io=fake_rom_io(&target);return rom_restart(&io);}
unsigned bridge_count(int which){switch(which){case 0:return target.boots;case 1:return target.resets;case 2:return target.go_count;case 3:return target.erase_count;case 4:return target.write_count;default:return target.forbidden_count;}}
unsigned bridge_units_erased(void){return target.units_erased;}
void bridge_corrupt(int on){target.corrupt_read=on;}
