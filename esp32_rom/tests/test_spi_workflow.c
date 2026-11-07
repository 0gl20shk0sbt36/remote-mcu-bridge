#include "spi_diagnostic.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
typedef struct {int mode,phase,opens,closes,acquires,releases,xfers,progress;bool owned,bus;uint32_t seq;int64_t now;} fake;
enum {SUCCESS,WRONG_DATA,WRONG_SEQ,READY_ERR,DONE_ERR,READY_TIMEOUT,DONE_TIMEOUT,OPEN_FAIL,XFER_FAIL,CLEAN_FAIL,DEADLINE,BUSY,PREP_FAIL,ARM_FAIL,READ_FAIL,PRIOR_BUSY};
static int64_t now(void *p) {return ((fake*)p)->now;}
static void progress(void *p) {((fake*)p)->progress++;}
static int open_bus(void *p,uint32_t hz,const char **err) {fake*f=p;assert(hz>=100000&&hz<=1000000);f->opens++;if(f->mode==OPEN_FAIL){*err="spi_bus_add_device";f->bus=true;return 17;}f->bus=true;if(f->mode==DEADLINE)f->now+=300000000;return 0;}
static int close_bus(void *p) {fake*f=p;f->closes++;if(f->mode==CLEAN_FAIL)return 18;f->bus=false;return 0;}
static bool busy(void *p) {return ((fake*)p)->bus;}
static bool acquire(void *p) {fake*f=p;assert(!f->owned);f->acquires++;if(f->mode==BUSY)return false;f->owned=true;return true;}
static void release(void *p) {fake*f=p;assert(f->owned);f->owned=false;f->releases++;}
static int prepare(void *p,const char **stage,const char **err) {fake*f=p;assert(f->owned);*stage="uart-drain";if(f->mode==PREP_FAIL){*err="bounded stale drain";return -1;}return 0;}
static int arm(void *p,uint32_t seq) {fake*f=p;assert(f->owned);f->seq=seq;f->phase=0;return f->mode==ARM_FAIL?-1:0;}
static int read_line(void *p,char *line,size_t n,int64_t deadline) {
 fake*f=p;assert(f->owned&&deadline>f->now);bool done=f->phase++>0;
 if(f->mode==READ_FAIL)return -1;
 if((f->mode==READY_TIMEOUT&&!done)||(f->mode==DONE_TIMEOUT&&done)){f->now=deadline;return 0;}
 if(f->mode==WRONG_SEQ){snprintf(line,n,"SPI READY %lu",(unsigned long)(f->seq+1));f->now+=250000;return 1;}
 if((f->mode==READY_ERR&&!done)||(f->mode==DONE_ERR&&done)){snprintf(line,n,"SPI DONE %lu ERR DATA i=7 got=0 expected=49",(unsigned long)f->seq);return 1;}
 snprintf(line,n,done?"SPI DONE %lu OK":"SPI READY %lu",(unsigned long)f->seq);f->now+=100;return 1;
}
static int transfer(void *p,const uint8_t tx[64],uint8_t rx[64],const char **err) {fake*f=p;assert(f->owned);f->xfers++;for(unsigned i=0;i<64;i++){assert(tx[i]==(uint8_t)(f->seq+i));rx[i]=(uint8_t)(f->seq^0xa5U^i);}if(f->mode==WRONG_DATA)rx[7]^=1;if(f->mode==XFER_FAIL){*err="SPI completion timeout/error";return 19;}return 0;}
static spi_diagnostic_result run(fake *f,uint32_t count,uint32_t hz,uint32_t *seq) {
 spi_diagnostic_io io={f,now,progress,open_bus,transfer,close_bus,busy,acquire,release,prepare,arm,read_line};spi_diagnostic_result r;spi_diagnostic_run(&io,count,hz,seq,&r);assert(!f->owned);assert(f->releases==f->acquires-(f->mode==BUSY?1:0));return r;
}
int main(void) {
 for(int mode=SUCCESS;mode<=PRIOR_BUSY;mode++) {
  fake f={.mode=mode,.bus=mode==PRIOR_BUSY};uint32_t seq=1;spi_diagnostic_result r=run(&f,3,100000,&seq);
  if(mode==SUCCESS){assert(r.ok&&r.completed==3&&r.esp_rx_verified==3&&r.stm_rx_verified==3&&!r.rx_mismatch_bytes&&seq==4);}
  else {assert(!r.ok);if(mode==CLEAN_FAIL){assert(r.completed==3&&r.cleanup_rc==18&&f.bus&&!strcmp(r.stage,"cleanup"));}else assert(!r.completed);}
  if(mode==WRONG_DATA)assert(r.rx_mismatch_bytes==1&&r.stm_rx_verified==1&&!strcmp(r.error,"ESP RX pattern mismatch"));
  if(mode==WRONG_SEQ||mode==READY_TIMEOUT)assert(!strcmp(r.error,"READY timeout")&&!f.xfers);
  if(mode==READY_ERR)assert(!strcmp(r.error,"STM ERR before READY"));
  if(mode==DONE_ERR)assert(!strcmp(r.error,"STM RX verification ERR"));
  if(mode==DONE_TIMEOUT)assert(!strcmp(r.error,"DONE timeout"));
  if(mode==OPEN_FAIL)assert(r.rc==17&&f.closes==1&&!f.acquires);
  if(mode==XFER_FAIL)assert(r.rc==19&&f.releases==1);
  if(mode==DEADLINE)assert(!strcmp(r.error,"total deadline")&&!f.acquires);
  if(mode==PRIOR_BUSY)assert(!f.opens&&!strcmp(r.error,"previous SPI cleanup failed; reboot required"));
 }
 const uint32_t invalid[][2]={{0,100000},{101,100000},{1,99999},{1,1000001},{UINT_MAX,UINT_MAX}};
 for(unsigned i=0;i<sizeof invalid/sizeof*invalid;i++){fake f={0};uint32_t seq=1;spi_diagnostic_result r=run(&f,invalid[i][0],invalid[i][1],&seq);assert(!r.ok&&!f.opens&&!f.closes&&seq==1);}
 for(unsigned hz=100000;hz<=1000000;hz+=450000){fake f={0};uint32_t seq=UINT_MAX;spi_diagnostic_result r=run(&f,100,hz,&seq);assert(r.ok&&r.completed==100&&seq==100&&r.last_seq==99&&r.deadline_ms==255000);}
 puts("PASS actual portable SPI workflow: 16 success/failure modes, 5 invalid bounds, 3x100 transfers, sequence wrap, exact data/counters, deadlines and balanced ownership");
}
