#include "rom.h"
#include "fake_rom.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The same suite runs against every supported part, so adding a device to
 * rom/devices.c immediately gets protocol coverage here. */
typedef struct { const rom_device *dev; uint32_t msp; } target_t;
static const target_t targets[] = {
 {&rom_device_f407zg, 0x20020000},
 {&rom_device_f103c8, 0x20005000},
};
static const target_t *T;
static fake_rom f;
static uint8_t image[0x100000];
static unsigned tests;

#define CHECK(c) do {tests++;if(!(c)){fprintf(stderr,"FAIL %s:%d [%s]: %s\n",__FILE__,__LINE__,T->dev->name,#c);exit(1);}}while(0)
static int source(void *c,uint32_t o,uint8_t *p,size_t n) {if(c)return -1;memcpy(p,image+o,n);return 0;}
static void word(unsigned o,uint32_t v){for(int j=0;j<4;j++)image[o+j]=(uint8_t)(v>>(8*j));}
static rom_source s={0,source};
static uint32_t base_of(int ram) {return ram?T->dev->image_base:T->dev->flash_base;}
static unsigned units(uint32_t n) {
 if(T->dev->sector_ends){unsigned c=1;while(c<T->dev->sector_count&&n>T->dev->sector_ends[c-1])c++;return c;}
 return (unsigned)((n+T->dev->page_size-1u)/T->dev->page_size);
}
static void init(int ram) {
 fake_rom_init_device(&f,T->dev);
 for(size_t j=0;j<sizeof image;j++)image[j]=(uint8_t)(j*17u);
 word(0,T->msp);word(4,base_of(ram)+9);
}
static int run(uint32_t n,int ram) {rom_io i=fake_rom_io(&f);return rom_download(&i,T->dev,&s,n,rom_crc32(image,n),ram);}

typedef struct {const char *stage,*phase;int rc,byte;uint32_t address;unsigned events;uint32_t version_raw;unsigned version_events;} trace_result;
static void capture(void *ctx,const char *stage,const char *phase,unsigned index,uint32_t address,int rc,int byte) {
 trace_result *r=ctx;(void)index;r->events++;
 if(!strcmp(phase,"version-options")) {r->version_raw=address;r->version_events++;}
 if(rc&&!r->rc) {r->stage=stage;r->phase=phase;r->rc=rc;r->byte=byte;r->address=address;}
}
static int traced(trace_result *r,int ram) {
 rom_io io=fake_rom_io(&f);
 return rom_download_traced(&io,T->dev,&s,32,rom_crc32(image,32),ram,capture,r);
}
static void diagnostics(void) {
 const char *ram_stages[]={"sync","Get","Get","GetVersion","GetVersion","GetID","GetID","write","write","write","read","read","read","go","go"};
 const char *flash_stages[]={"sync","Get","Get","GetVersion","GetVersion","GetID","GetID","erase","erase","write","write","write","read","read","read"};
 for(int ram=0;ram<=1;ram++) for(unsigned k=0;k<15;k++) {
  init(ram);f.bad_ack_at=k+1;trace_result r={0};
  CHECK(traced(&r,ram)==ROM_E_ACK);CHECK(r.rc==ROM_E_ACK);CHECK(r.byte==0x1f);
  CHECK(!strcmp(r.stage,ram?ram_stages[k]:flash_stages[k]));
  CHECK(strstr(r.phase,"ACK")!=NULL);
  /* Fake processes Go's address before injecting its ACK fault; no resend/reset. */
  CHECK(f.go_count==(unsigned)(ram&&k==14)&&f.resets==1);
 }
 init(1);f.timeout=1;trace_result t={0};CHECK(traced(&t,1)==ROM_E_TIMEOUT);
 CHECK(t.rc==ROM_E_TIMEOUT&&t.byte==-1&&!strcmp(t.stage,"sync"));
 init(1);f.corrupt_offset=17;trace_result m={0};CHECK(traced(&m,1)==ROM_E_VERIFY);
 CHECK(!strcmp(m.phase,"verify-first-mismatch")&&m.address==T->dev->image_base+17);
 init(1);f.fragment=1;trace_result good={0};CHECK(traced(&good,1)==0);CHECK(!good.rc&&good.events>20&&f.go_count==1);
}
static void protocol(void) {
 /* RAM image: written, verified, jumped to; no erase, no second reset. */
 init(1);f.fragment=1;
 CHECK(run(259,1)==0);
 CHECK(f.go_count==1&&f.read_count==2&&f.erase_count==0);
 CHECK(!memcmp(f.ram+(T->dev->image_base-T->dev->ram_base),image,259));
 CHECK(f.ram[(T->dev->image_base-T->dev->ram_base)+259]==0xff);
 CHECK(f.resets==1&&f.boot0==1);

 /* Flash image: erase only the covering units, the next unit is preserved. */
 init(0);
 {
  unsigned next=T->dev->sector_ends?T->dev->sector_ends[0]:T->dev->page_size;
  f.flash[next]=0x5a;
  CHECK(run(259,0)==0);
  CHECK(f.units_erased==1&&f.erase_count==1);
  CHECK(f.flash[next]==0x5a);
 }
 CHECK(f.go_count==0&&f.resets==2&&f.boot0==0);
 CHECK(!memcmp(f.flash,image,259));

 /* Erase encoding: the non-preferred command must work too. */
 init(0);f.extended_erase=!f.extended_erase;CHECK(run(256,0)==0);CHECK(f.units_erased==1);

 /* Full-image coverage erases exactly every unit of the part. */
 init(0);
 CHECK(run(T->dev->flash_size,0)==0);
 CHECK(f.units_erased==units(T->dev->flash_size));
 CHECK(f.forbidden_count==0);

 /* Failures must never reach Go. */
 init(1);f.bad_ack=1;CHECK(run(32,1)==ROM_E_ACK);CHECK(f.go_count==0&&f.write_count==0);
 init(1);f.timeout=1;CHECK(run(32,1)==ROM_E_TIMEOUT);CHECK(f.go_count==0);
 init(0);f.id=(uint16_t)(T->dev->device_id^0xffff);CHECK(run(32,0)==ROM_E_ID);CHECK(!f.erase_count&&!f.write_count&&f.resets==1);
 init(0);f.missing_read=1;CHECK(run(32,0)==ROM_E_CAP);CHECK(!f.erase_count&&!f.write_count);
 init(1);f.corrupt_read=1;CHECK(run(512,1)==ROM_E_VERIFY);CHECK(!f.go_count);
 init(1);f.corrupt_padding=1;CHECK(run(259,1)==ROM_E_VERIFY);CHECK(!f.go_count);
 init(0);f.corrupt_read=1;CHECK(run(32,0)==ROM_E_VERIFY);CHECK(f.resets==1&&f.boot0==1);
 init(0);{rom_io io=fake_rom_io(&f);CHECK(rom_download(&io,T->dev,&s,32,0,0)==ROM_E_CRC);}
 CHECK(!f.boots&&!f.resets&&!f.erase_count);
 init(1);s.ctx=(void *)1;CHECK(run(32,1)==ROM_E_SOURCE);CHECK(!f.boots);s.ctx=0;
 init(1);f.fail_tx=1;CHECK(run(32,1)==ROM_E_IO);CHECK(!f.go_count);
 init(1);f.fail_boot=1;CHECK(run(32,1)==ROM_E_IO);CHECK(!f.resets);
 init(1);f.fail_reset=1;CHECK(run(32,1)==ROM_E_IO);CHECK(!f.go_count);

 /* Argument and geometry limits. */
 CHECK(rom_validate(T->dev,&s,0,0,0)==ROM_E_ARG);
 CHECK(rom_validate(T->dev,&s,7,0,0)==ROM_E_ARG);
 CHECK(rom_validate(T->dev,&s,T->dev->flash_size+1,0,0)==ROM_E_GEOMETRY);
 CHECK(rom_validate(T->dev,&s,T->dev->image_max+1,0,1)==ROM_E_ARG);
 CHECK(rom_validate(T->dev,&s,0xffffffff,0,1)==ROM_E_ARG);

 /* Vector table checks. */
 init(1);word(0,0x10010000);CHECK(run(32,1)==ROM_E_VECTOR);CHECK(!f.boots); /* CCM-like */
 word(0,T->msp+8);CHECK(run(32,1)==ROM_E_VECTOR);
 word(0,T->msp-1);CHECK(run(32,1)==ROM_E_VECTOR);
 word(0,T->dev->ram_base);CHECK(run(32,1)==ROM_E_VECTOR);
 word(0,T->msp);word(4,T->dev->image_base+8);CHECK(run(32,1)==ROM_E_VECTOR);
 word(4,T->dev->image_base+33);CHECK(run(32,1)==ROM_E_VECTOR);
 word(4,T->dev->image_base+1);CHECK(run(32,1)==ROM_E_VECTOR);
 word(4,0x10000009);CHECK(run(32,1)==ROM_E_VECTOR);
 word(4,T->dev->image_base+31);CHECK(run(31,1)==0); /* last real byte valid */
 init(1);word(4,T->dev->image_base+33);CHECK(run(33,1)==0);
 word(4,T->dev->image_base+35);CHECK(run(33,1)==ROM_E_VECTOR);

 /* Stack that would overwrite the image is rejected (RAM only). */
 init(1);word(4,T->dev->image_base+9);word(0,T->dev->image_base+0x40);
 CHECK(run(256,1)==ROM_E_VECTOR);

 /* Every ACK stage failing must abort without Go. */
 for(unsigned stage=1;stage<=13;stage++) {init(1);f.bad_ack_at=stage;CHECK(run(32,1)==ROM_E_ACK);CHECK(!f.go_count);}
 for(unsigned stage=1;stage<=15;stage++) {init(0);f.bad_ack_at=stage;CHECK(run(32,0)==ROM_E_ACK);CHECK(f.resets==1&&f.boot0==1);}

 /* Readback mismatch at any byte offset is caught. */
 for(uint32_t offset=0;offset<260;offset++) {init(1);f.corrupt_offset=offset;CHECK(run(259,1)==ROM_E_VERIFY);CHECK(!f.go_count);}

 CHECK(f.forbidden_count==0);
}

static int junk_rx(void *ctx,uint8_t *b,size_t n,uint32_t ms) {
 (void)ctx;(void)n;(void)ms;*b=0xb0;return 1;
}
static void probe_tests(void) {
 rom_probe_result p;rom_io io;trace_result t;
 init(0);io=fake_rom_io(&f);f.fragment=1;
 uint32_t flash_crc=rom_crc32(f.flash,T->dev->flash_size);
 CHECK(rom_probe(&io,T->dev,&p,NULL,NULL)==0);
 CHECK(p.commands==3&&p.device_id==T->dev->device_id&&p.version!=0);
 CHECK(p.version_queried&&p.get_version==p.version&&!p.version_mismatch&&!p.version_zero);
 CHECK(f.resets==1&&f.boot0==1&&f.boots==1);
 CHECK(!f.erase_count&&!f.write_count&&!f.read_count&&!f.go_count&&!f.forbidden_count);
 CHECK(rom_crc32(f.flash,T->dev->flash_size)==flash_crc);
 /* Read capability not required for a read-only diagnostic. */
 init(0);io=fake_rom_io(&f);f.missing_read=1;CHECK(rom_probe(&io,T->dev,&p,NULL,NULL)==0);
 for(unsigned k=1;k<=7;k++) {
  init(0);io=fake_rom_io(&f);f.bad_ack_at=k;t=(trace_result){0};
  CHECK(rom_probe(&io,T->dev,&p,capture,&t)==ROM_E_ACK);
  CHECK(t.byte==0x1f&&f.resets==1);
  CHECK(!f.erase_count&&!f.write_count&&!f.read_count&&!f.go_count&&!f.forbidden_count);
 }
 /* Zero remains fatal. Independent nonzero version values are accepted. */
 for(unsigned v=0;v<2;v++) {
  init(0);io=fake_rom_io(&f);f.fragment=1;f.get_version=0x10;
  f.version_reply[0]=v?0x22:0;f.version_reply[1]=0xa5;f.version_reply[2]=0x5a;
  t=(trace_result){0};
  CHECK(rom_probe(&io,T->dev,&p,capture,&t)==(v?0:ROM_E_PROTOCOL));
  CHECK(p.commands==3&&p.device_id==T->dev->device_id&&p.version==0x10);
  CHECK(p.get_version==f.version_reply[0]&&p.option1==0xa5&&p.option2==0x5a);
  CHECK(p.version_mismatch&&p.version_zero==!v&&p.version_queried);
  CHECK(t.version_events==1&&t.version_raw==(0x10000000u|((uint32_t)f.version_reply[0]<<16)|0xa55au));
  CHECK(!f.erase_count&&!f.write_count&&!f.read_count&&!f.go_count&&!f.forbidden_count&&f.resets==1);
  if(!v) CHECK(t.byte==-1&&!strcmp(t.phase,"version-zero"));
  init(0);f.get_version=0x10;f.version_reply[0]=v?0x22:0;
  f.version_reply[1]=0xa5;f.version_reply[2]=0x5a;t=(trace_result){0};
  CHECK(traced(&t,0)==(v?0:ROM_E_PROTOCOL));
  CHECK(t.version_events==1);
  if(v) {CHECK(f.erase_count==1&&f.write_count>0&&f.resets==2&&!f.go_count);}
  else {CHECK(t.byte==-1&&!strcmp(t.phase,"version-zero"));CHECK(!f.erase_count&&!f.write_count&&!f.go_count&&f.resets==1);}
 }
 /* 0x10 alone is not rejected or interpreted as a hardware identity. */
 init(0);io=fake_rom_io(&f);f.get_version=f.version_reply[0]=0x10;
 CHECK(rom_probe(&io,T->dev,&p,NULL,NULL)==0);
 init(0);io=fake_rom_io(&f);io.rx=junk_rx;t=(trace_result){0};
 CHECK(rom_probe(&io,T->dev,&p,capture,&t)==ROM_E_ACK);
 CHECK(t.byte==0xb0&&p.commands==0&&f.resets==1&&!f.write_count&&!f.erase_count);
 init(0);io=fake_rom_io(&f);f.timeout=1;
 CHECK(rom_probe(&io,T->dev,&p,NULL,NULL)==ROM_E_TIMEOUT);CHECK(p.commands==0&&f.resets==1);
 init(0);io=fake_rom_io(&f);f.id^=0xffff;
 CHECK(rom_probe(&io,T->dev,&p,NULL,NULL)==ROM_E_ID);CHECK(!f.erase_count&&!f.write_count&&!f.go_count);
 init(0);io=fake_rom_io(&f);f.fail_boot=1;
 CHECK(rom_probe(&io,T->dev,&p,NULL,NULL)==ROM_E_IO);CHECK(!f.resets);
 CHECK(rom_probe(NULL,T->dev,&p,NULL,NULL)==ROM_E_ARG);
 CHECK(rom_probe(&io,NULL,&p,NULL,NULL)==ROM_E_ARG);
 CHECK(rom_probe(&io,T->dev,NULL,NULL,NULL)==ROM_E_ARG);
}
int main(void) {
 CHECK(rom_crc32((const uint8_t *)"123456789",9)==0xcbf43926u);
 for(size_t k=0;k<sizeof targets/sizeof targets[0];k++) {T=&targets[k];protocol();diagnostics();probe_tests();}
 printf("PASS: %u assertions over %zu devices (AN3155 fake UART, Flash/SRAM, failures)\n",
        tests,sizeof targets/sizeof targets[0]);
 return 0;
}
