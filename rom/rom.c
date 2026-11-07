#include "rom.h"
#include <string.h>
/* Protocol: AN3155 Rev22 §§3.1-3.8. Device facts live in the rom_device table:
 *   STM32F407ZG - 1 MiB, 4x16K + 1x64K + 7x128K sectors (RM0090), PID 0x413
 *                 = F405/407/415/417 family, not a SKU check.
 *   STM32F103C8 - 64 KiB, uniform 1 KiB pages (RM0008), PID 0x410 = medium
 *                 density; ROM loader owns the first 512 bytes of SRAM
 *                 (AN2606 Rev70 Table 45) and NACKs accesses below that.
 * Only erase units intersecting the image are erased; their unused tails are
 * NOT preserved. No mass erase, protection or unprotection commands.
 */
#define TRY(x) do { int e_=(x); if(e_) return e_; } while(0)
static uint32_t crc_step(uint32_t c,const uint8_t *p,size_t n) {
 while(n--) { c^=*p++; for(int k=0;k<8;k++) c=(c>>1)^((0u-(c&1u))&0xedb88320u); } return c;
}
uint32_t rom_crc32(const uint8_t *p,size_t n) { return ~crc_step(~0u,p,n); }
static uint32_t le32(const uint8_t *p) { return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
/* Number of erase units the image [0,n) intersects. */
static unsigned units_for(const rom_device *d,uint32_t n) {
 if(d->sector_ends) { unsigned c=1; while(c<d->sector_count&&n>d->sector_ends[c-1]) c++; return c; }
 return (unsigned)((n+d->page_size-1u)/d->page_size);
}
int rom_validate(const rom_device *d,const rom_source *s,uint32_t n,uint32_t crc,int ram) {
 uint8_t b[256],v[8]; uint32_t c=~0u;
 if(!d||!s||!s->read||(ram!=0&&ram!=1)||n<8) return ROM_E_ARG;
 if(ram) { if(n>d->image_max) return ROM_E_ARG; }
 else if(n>d->flash_size) return ROM_E_GEOMETRY;
 uint32_t base=ram?d->image_base:d->flash_base;
 if(s->read(s->ctx,0,v,8)) return ROM_E_SOURCE;
 uint32_t sp=le32(v),pc=le32(v+4),entry=pc&~1u;
 /* MSP: 8-aligned, inside this device's executable SRAM, above the ROM
  * loader's own footprint (RAM images) or above nothing (Flash images). */
 if((sp&7u)||sp<=d->ram_base+(ram?d->image_base-d->ram_base:0u)||sp>d->ram_base+d->ram_size) return ROM_E_VECTOR;
 if(!(pc&1u)||entry<base+8u||entry>=base+n) return ROM_E_VECTOR;
 /* For a RAM image the stack must start at or above the image end, otherwise
  * the first push overwrites code. Linker scripts reserve stack/heap at the
  * top of the image, so this holds by construction when the layout is right. */
 if(ram&&sp<base+n) return ROM_E_VECTOR;
 for(uint32_t o=0;o<n;) { size_t z=n-o; if(z>sizeof b) z=sizeof b; if(s->read(s->ctx,o,b,z)) return ROM_E_SOURCE; c=crc_step(c,b,z); o+=(uint32_t)z; }
 return ~c==crc?ROM_OK:ROM_E_CRC;
}
typedef struct {
 const rom_io *io;
 rom_trace_fn trace;
 void *trace_ctx;
 const char *stage;
 unsigned command_index;
 uint32_t address;
} session;
static void event(const session *i,const char *phase,int status,int byte) {
 if(i->trace) i->trace(i->trace_ctx,i->stage,phase,i->command_index,i->address,status,byte);
}
static int tx(const session *i,const uint8_t *p,size_t n) {
 while(n) { int k=i->io->tx(i->io->ctx,p,n); if(k<=0||(size_t)k>n) return ROM_E_IO; p+=k;n-=(size_t)k; } return 0;
}
static int rx(const session *i,uint8_t *p,size_t n,uint32_t t) {
 /* Bound partial-read attempts; each call is at most t milliseconds. */
 while(n) { int k=i->io->rx(i->io->ctx,p,n,t); if(!k) {event(i,"receive-timeout",ROM_E_TIMEOUT,-1);return ROM_E_TIMEOUT;} if(k<0||(size_t)k>n) {event(i,"receive-error",ROM_E_IO,-1);return ROM_E_IO;} p+=k;n-=(size_t)k; } return 0;
}
static int ack(const session *i,uint32_t t,const char *phase) { uint8_t b=0;int rc=rx(i,&b,1,t);if(!rc&&b!=0x79) rc=ROM_E_ACK;event(i,phase,rc,rc==ROM_E_IO||rc==ROM_E_TIMEOUT?-1:b);return rc; }
static int cmd(session *i,uint8_t c) {
 i->stage=c==0?"Get":c==1?"GetVersion":c==2?"GetID":c==0x43||c==0x44?"erase":c==0x31?"write":c==0x11?"read":c==0x21?"go":"unknown";
 ++i->command_index;event(i,"command",0,-1);
 uint8_t b[2]={c,(uint8_t)~c};TRY(tx(i,b,2));return ack(i,1000,"command-ACK");
}
static int addr(session *i,uint32_t a) { uint8_t b[5]={(uint8_t)(a>>24),(uint8_t)(a>>16),(uint8_t)(a>>8),(uint8_t)a,0};for(int j=0;j<4;j++) b[4]^=b[j];i->address=a;TRY(tx(i,b,5));return ack(i,1000,"address-ACK"); }
/* AN3155 Rev22: ACK, three version/option bytes, ACK (no length byte).
 * Pack prior Get and all three GetVersion bytes into diagnostic metadata. */
static int get_version(session *i,uint8_t prior,uint8_t v[3]) {
 TRY(cmd(i,1));TRY(rx(i,v,3,1000));TRY(ack(i,1000,"data-ACK"));
 i->address=((uint32_t)prior<<24)|((uint32_t)v[0]<<16)|((uint32_t)v[1]<<8)|v[2];
 event(i,"version-options",0,-1);i->address=0;return 0;
}
static int check_version(session *i,uint8_t prior,const uint8_t v[3]) {
 if(!v[0]||v[0]!=prior) {
  i->stage="GetVersion";
  i->address=((uint32_t)prior<<24)|((uint32_t)v[0]<<16)|((uint32_t)v[1]<<8)|v[2];
  /* ST host example stores both independently. Nonzero differences are
   * diagnostic warnings, not proof of a corrupt response. Zero stays fatal. */
  event(i,!v[0]?"version-zero":"version-mismatch",!v[0]?ROM_E_PROTOCOL:0,-1);
  i->address=0;return !v[0]?ROM_E_PROTOCOL:0;
 }
 return 0;
}
static int discover(session *i,const rom_device *d,uint8_t *erase,int ram) {
 uint8_t n,b[256],caps[256]={0},v[3];TRY(cmd(i,0));TRY(rx(i,&n,1,1000));TRY(rx(i,b,(size_t)n+1,1000));TRY(ack(i,1000,"data-ACK"));
 for(unsigned j=1;j<=(unsigned)n;j++) caps[b[j]]=1;
 i->address=b[0];event(i,"bootloader-version",0,-1);
 i->address=(caps[0]?1u:0u)|(caps[1]?2u:0u)|(caps[2]?4u:0u)|(caps[0x11]?8u:0u)|(caps[0x31]?16u:0u)|(caps[0x21]?32u:0u)|(caps[0x43]?64u:0u)|(caps[0x44]?128u:0u);
 event(i,"capabilities-mask",0,-1);i->address=0;
 if(!caps[1]||!caps[2]||!caps[0x11]||!caps[0x31]||!caps[0x21]) return ROM_E_CAP;
 if(!ram) {
  /* Pick the first erase command this device prefers that the ROM advertises
   * and that can encode our unit ids (classic erase uses one byte each). */
  unsigned units=units_for(d,d->flash_size);
  int chosen=0;
  for(unsigned k=0;k<2&&!chosen;k++) {
   uint8_t c=d->erase_order[k];
   if(!caps[c]) continue;
   if(c==0x43&&units>256) continue;
   if(c==0x44&&units>65536) continue;
   *erase=c;chosen=1;
  }
  if(!chosen) return ROM_E_CAP;
 }
 TRY(get_version(i,b[0],v));TRY(check_version(i,b[0],v));
 TRY(cmd(i,2));TRY(rx(i,&n,1,1000));if(n!=1) return ROM_E_ID;TRY(rx(i,b,2,1000));TRY(ack(i,1000,"data-ACK"));
 i->address=((uint32_t)b[0]<<8)|b[1];event(i,"device-id",0,-1);i->address=0;
 if((uint16_t)(((uint16_t)b[0]<<8)|b[1])!=d->device_id) return ROM_E_ID;
 return 0;
}
static int erase_image(session *i,const rom_device *d,uint32_t n,uint8_t c) {
 unsigned count=units_for(d,n);
 /* Worst case: 1 count byte or 2, then 2 bytes per unit, then checksum. */
 uint8_t b[3+512],x=0;size_t z=0;
 if(c==0x44) {if(count>256) return ROM_E_GEOMETRY;b[z++]=0;b[z++]=(uint8_t)(count-1);}
 else {if(count>256) return ROM_E_GEOMETRY;b[z++]=(uint8_t)(count-1);}
 TRY(cmd(i,c));i->address=count;event(i,"covered-erase-units",0,-1);i->address=0;
 for(unsigned k=0;k<count;k++) {
  if(c==0x44) {b[z++]=(uint8_t)(k>>8);b[z++]=(uint8_t)k;}
  else {if(k>255) return ROM_E_GEOMETRY;b[z++]=(uint8_t)k;}
 }
 for(size_t k=0;k<z;k++) { x^=b[k]; }
 b[z++]=x;TRY(tx(i,b,z));return ack(i,120000,"erase-complete-ACK");
}
static int probe(session *i,const rom_device *d,rom_probe_result *r) {
 const rom_io *io=i->io;uint8_t sync=0x7f,n,b[256],v[3];
 if(!io||!d||!r||!io->tx||!io->rx||!io->boot||!io->reset||!io->sleep_ms) return ROM_E_ARG;
 i->stage="boot-reset";event(i,"start",0,-1);
 TRY(io->boot(io->ctx,1)?ROM_E_IO:0);TRY(io->reset(io->ctx)?ROM_E_IO:0);io->sleep_ms(io->ctx,100);
 i->stage="sync";event(i,"command",0,-1);TRY(tx(i,&sync,1));TRY(ack(i,1000,"sync-ACK"));
 TRY(cmd(i,0));TRY(rx(i,&n,1,1000));TRY(rx(i,b,(size_t)n+1,1000));TRY(ack(i,1000,"data-ACK"));
 r->version=b[0];i->address=b[0];event(i,"bootloader-version",0,-1);i->address=0;
 int has_id=0,has_version=0;for(unsigned k=1;k<=(unsigned)n;k++) {if(b[k]==2) has_id=1;if(b[k]==1) has_version=1;}
 if(!has_id||!has_version) {event(i,"missing-probe-capability",ROM_E_CAP,-1);return ROM_E_CAP;}
 TRY(get_version(i,r->version,v));
 r->get_version=v[0];r->option1=v[1];r->option2=v[2];r->version_queried=1;
 r->version_mismatch=v[0]!=r->version;r->version_zero=!v[0];
 TRY(cmd(i,2));TRY(rx(i,&n,1,1000));if(n!=1) return ROM_E_ID;
 TRY(rx(i,b,2,1000));TRY(ack(i,1000,"data-ACK"));
 r->device_id=(uint16_t)(((uint16_t)b[0]<<8)|b[1]);i->address=r->device_id;event(i,"device-id",0,-1);i->address=0;
 /* Finish nondestructive identity collection even on semantic mismatch. */
 TRY(check_version(i,r->version,v));
 if(r->device_id!=d->device_id) {event(i,"device-id-mismatch",ROM_E_ID,-1);return ROM_E_ID;}
 return 0;
}
int rom_probe(const rom_io *io,const rom_device *d,rom_probe_result *r,rom_trace_fn trace,void *ctx) {
 session state={.io=io,.trace=trace,.trace_ctx=ctx,.stage="probe"};
 if(r) memset(r,0,sizeof *r);
 int rc=probe(&state,d,r);if(r) r->commands=state.command_index;
 event(&state,"result",rc,-1);return rc;
}
int rom_restart(const rom_io *i) { if(!i||!i->boot||!i->reset) return ROM_E_ARG;TRY(i->boot(i->ctx,0)?ROM_E_IO:0);return i->reset(i->ctx)?ROM_E_IO:0; }
static int download(session *i,const rom_device *d,const rom_source *s,uint32_t n,uint32_t crc,int ram) {
 const rom_io *io=i->io;
 uint8_t b[258],r[256],erase=0,sync=0x7f;
 if(!io||!io->tx||!io->rx||!io->boot||!io->reset||!io->sleep_ms) return ROM_E_ARG;
 TRY(rom_validate(d,s,n,crc,ram));
 uint32_t padded=(n+3u)&~3u,base=ram?d->image_base:d->flash_base;
 i->stage="boot-reset";event(i,"start",0,-1);TRY(io->boot(io->ctx,1)?ROM_E_IO:0);TRY(io->reset(io->ctx)?ROM_E_IO:0);io->sleep_ms(io->ctx,100);
 i->stage="sync";event(i,"command",0,-1);TRY(tx(i,&sync,1));TRY(ack(i,1000,"sync-ACK"));TRY(discover(i,d,&erase,ram));if(!ram) TRY(erase_image(i,d,padded,erase));
 for(uint32_t o=0;o<padded;) {
  size_t z=padded-o;if(z>256) z=256;size_t actual=n>o?n-o:0;if(actual>z) actual=z;
  b[0]=(uint8_t)(z-1);memset(b+1,0xff,z);if(actual&&s->read(s->ctx,o,b+1,actual)) return ROM_E_SOURCE;
  b[z+1]=b[0];for(size_t k=1;k<=z;k++) b[z+1]^=b[k];
  i->address=base+o;TRY(cmd(i,0x31));TRY(addr(i,base+o));TRY(tx(i,b,z+2));TRY(ack(i,1000,"data-ACK"));o+=(uint32_t)z;
 }
 /* Full second pass verification, including every padded byte, before execution. */
 for(uint32_t o=0;o<padded;) {
  size_t z=padded-o;if(z>256) z=256;size_t actual=n>o?n-o:0;if(actual>z) actual=z;
  memset(b,0xff,z);if(actual&&s->read(s->ctx,o,b,actual)) return ROM_E_SOURCE;
  i->address=base+o;TRY(cmd(i,0x11));TRY(addr(i,base+o));uint8_t len[2]={(uint8_t)(z-1),(uint8_t)~(z-1)};TRY(tx(i,len,2));TRY(ack(i,1000,"data-ACK"));TRY(rx(i,r,z,1000));if(memcmp(b,r,z)) {size_t k=0;while(k<z&&b[k]==r[k]) k++;i->address=base+o+(uint32_t)k;event(i,"verify-first-mismatch",ROM_E_VERIFY,-1);return ROM_E_VERIFY;}o+=(uint32_t)z;
 }
 i->address=base+padded;event(i,"verify-complete",0,-1);
 if(ram) {i->address=base;TRY(cmd(i,0x21));return addr(i,base);}i->stage="flash-restart";event(i,"start",0,-1);return rom_restart(io);
}
int rom_download_traced(const rom_io *io,const rom_device *d,const rom_source *s,uint32_t n,uint32_t crc,int ram,rom_trace_fn trace,void *ctx) {
 session state={.io=io,.trace=trace,.trace_ctx=ctx,.stage="validate"};
 int rc=download(&state,d,s,n,crc,ram);
 event(&state,"result",rc,-1);
 return rc;
}
int rom_download(const rom_io *io,const rom_device *d,const rom_source *s,uint32_t n,uint32_t crc,int ram) {
 return rom_download_traced(io,d,s,n,crc,ram,NULL,NULL);
}
const char *rom_strerror(int e) {switch(e){case 0:return "ok";case ROM_E_ARG:return "invalid image size/argument";case ROM_E_SOURCE:return "image source read failed";case ROM_E_CRC:return "image CRC mismatch";case ROM_E_VECTOR:return "invalid stack/reset vector";case ROM_E_IO:return "UART/GPIO I/O failed";case ROM_E_TIMEOUT:return "UART timeout";case ROM_E_ACK:return "ROM NACK/unexpected ACK";case ROM_E_ID:return "unexpected device ID";case ROM_E_CAP:return "missing ROM capability";case ROM_E_VERIFY:return "readback mismatch";case ROM_E_GEOMETRY:return "image exceeds device geometry";default:return "invalid ROM protocol";}}
