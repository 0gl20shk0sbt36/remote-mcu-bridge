#include "module_abi.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(int argc,char **argv) {
 assert(argc==2);FILE *f=fopen(argv[1],"rb");assert(f);module_header h;assert(fread(&h,1,sizeof h,f)==sizeof h);fclose(f);
 assert(module_header_valid(&h,0x100000,h.text_addr,h.data_addr));
 for(unsigned k=0;k<10;k++) { module_header v=h;((uint32_t *)&v)[k]=0xffffffffu;assert(!module_header_valid(&v,0x100000,h.text_addr,h.data_addr)); }
 module_header empty;memset(&empty,255,sizeof empty);assert(!module_header_valid(&empty,0x100000,h.text_addr,h.data_addr));
 memset(&empty,0,sizeof empty);assert(!module_header_valid(&empty,0x100000,h.text_addr,h.data_addr));
 module_header v=h;v.entry=h.text_addr+h.text_size;assert(!module_header_valid(&v,0x100000,h.text_addr,h.data_addr));v=h;v.bss_size=MODULE_DRAM_SIZE-v.data_size+1;assert(!module_header_valid(&v,0x100000,h.text_addr,h.data_addr));
 assert(!module_header_valid(&h,4095,h.text_addr,h.data_addr));assert(!module_header_valid(&h,0x100000,h.text_addr+4,h.data_addr));
 puts("production module_header_valid: real image accepted; empty/corrupt/bounds/address cases rejected");return 0;
}
