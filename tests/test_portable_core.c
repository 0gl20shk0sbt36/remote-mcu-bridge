/* Native tests of production core, not a reimplementation of the updater. */
#include "bridge/update.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SIZE 2048u
#define FOOT 512u
#define BODY (SIZE-FOOT)
#define IMAGE 547u
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr,"FAIL %s:%d: %s\n",__FILE__,__LINE__,#x); exit(1); } } while (0)
static unsigned checks, cases, injections;
typedef struct {
 unsigned char bytes[SIZE];
 size_t calls, fail, body_programs, footer_programs;
 char trace[512];
 int format_error;
 int read_error;
 int drop_program;
 int partial;
} mock;
static int event(mock *m,char kind) {
 CHECK(m->calls < sizeof m->trace);
 m->trace[m->calls++]=kind;
 return m->fail==m->calls ? BRIDGE_E_IO : 0;
}
static void bounds(size_t p,size_t n) { CHECK(p<=SIZE); CHECK(n<=SIZE-p); }
static int rd(void *ctx,size_t p,void *b,size_t n) {
 mock *m=ctx; bounds(p,n); int e=event(m,'r'); if(e)return m->read_error?m->read_error:e; memcpy(b,m->bytes+p,n);return 0;
}
static int erase(void *ctx,size_t p,size_t n) {
 mock *m=ctx; bounds(p,n);CHECK(p%256==0);CHECK(n%256==0);
 int e=event(m,p==0?'E':'e');if(e){if(m->partial)memset(m->bytes+p,255,n/2);return e;}memset(m->bytes+p,255,n);return 0;
}
static int program(void *ctx,size_t p,const void *v,size_t n) {
 mock *m=ctx;const unsigned char *b=v; bounds(p,n);CHECK(p%16==0);CHECK(n==16);
 int e=event(m,p<BODY?'w':'f');if(e){if(m->partial)for(size_t i=0;i<n/2;i++)m->bytes[p+i]&=b[i];return e;}
 if((m->drop_program==1&&p<BODY)||(m->drop_program==2&&p>=BODY))return 0;
 for(size_t i=0;i<n;i++){CHECK((m->bytes[p+i]&b[i])==b[i]);m->bytes[p+i]&=b[i];}
 if(p<BODY)++m->body_programs;else ++m->footer_programs;return 0;
}
/* Deliberately noncryptographic: tests callback data/ranges and digest comparison. */
static void digest(const unsigned char *b,size_t n,unsigned char out[32]) {
 memset(out,0,32);for(size_t i=0;i<n;i++)out[i%32]=(unsigned char)(out[i%32]*33u+b[i]+(unsigned char)i);
}
static int sha(void *ctx,size_t p,size_t n,unsigned char out[32]) {
 mock *m=ctx;bounds(p,n);CHECK(p==0);CHECK(n==IMAGE || n==BODY || n==17);
 int e=event(m,'h');if(e)return e;digest(m->bytes+p,n,out);return 0;
}
static int format(void *ctx,size_t n) {
 mock *m=ctx;CHECK(n>=16&&n<=BODY);int e=event(m,'c');return e?e:m->format_error;
}
static int sync_store(void *ctx) {return event(ctx,'s');}
static bridge_storage_ops ops={SIZE,256,16,rd,erase,program,sha,format,sync_store};
static void fresh(mock *m,bridge_update *u,int committed) {
 memset(m,0,sizeof *m);memset(m->bytes,0xa5,BODY);memset(m->bytes+BODY,committed?0:255,FOOT);
 CHECK(bridge_update_init(u,&ops,m,FOOT,16)==0);
}
static void reboot(mock *m,bridge_update *u) {
 m->fail=0;CHECK(bridge_update_init(u,&ops,m,FOOT,16)==0);CHECK(bridge_update_boot(u)==0);
}
static int is_commit(const mock *m) {for(size_t i=BODY;i<SIZE;i++)if(m->bytes[i])return 0;return 1;}
static void image(unsigned char *b,size_t n) {for(size_t i=0;i<n;i++)b[i]=(unsigned char)(i*19u+7u);}
static int upload(bridge_update *u,const unsigned char *b,size_t n,unsigned seed) {
 int e=bridge_update_begin(u,n);if(e)return e;
 size_t p=0;while(p<n){seed=seed*1664525u+1013904223u;size_t k=1+seed%73u;if(k>n-p)k=n-p;
 e=bridge_update_write(u,p,b+p,k);if(e)return e;p+=k;}
 unsigned char hash[32];digest(b,n,hash);return bridge_update_finish(u,hash);
}
static void successful_fragments(void) {
 unsigned char b[BODY];image(b,sizeof b);
 for(unsigned seed=0;seed<64;seed++) {
  mock m;bridge_update u;fresh(&m,&u,0);CHECK(bridge_update_boot(&u)==0);
  size_t n=seed==0?BODY:seed==1?17:IMAGE;
  CHECK(upload(&u,b,n,seed)==0);CHECK(memcmp(m.bytes,b,n)==0);
  size_t padded=(n+15u)/16u*16u;for(size_t p=n;p<padded;p++)CHECK(m.bytes[p]==255);
  CHECK(is_commit(&m));CHECK(!bridge_update_allowed(&u));CHECK(!bridge_execution_allowed(&u));
  CHECK(bridge_update_begin(&u,n)==BRIDGE_E_STATE);reboot(&m,&u);
  CHECK(bridge_execution_allowed(&u));CHECK(!bridge_update_allowed(&u));++cases;
 }
}
static void boot_and_invalidate(void) {
 mock m;bridge_update u;unsigned char b[IMAGE];image(b,sizeof b);fresh(&m,&u,1);
 CHECK(!bridge_update_allowed(&u));CHECK(!bridge_execution_allowed(&u));
 CHECK(bridge_update_boot(&u)==0);CHECK(bridge_execution_allowed(&u));
 size_t before=m.calls;CHECK(bridge_update_begin(&u,IMAGE)==BRIDGE_E_STATE);CHECK(m.calls==before);
 CHECK(bridge_update_write(&u,0,b,16)==BRIDGE_E_STATE);
 CHECK(bridge_update_invalidate(&u)==0);for(size_t i=0;i<BODY;i++)CHECK(m.bytes[i]==0xa5);
 CHECK(bridge_execution_allowed(&u));CHECK(!bridge_update_allowed(&u));
 CHECK(bridge_update_boot(&u)==0);CHECK(!bridge_update_allowed(&u));
 reboot(&m,&u);CHECK(bridge_update_allowed(&u));CHECK(!bridge_execution_allowed(&u));
 CHECK(bridge_update_begin(&u,IMAGE)==0);CHECK(bridge_update_write(&u,0,b,19)==0);
 reboot(&m,&u);CHECK(bridge_update_allowed(&u));CHECK(!bridge_execution_allowed(&u));
 for(size_t i=0;i<FOOT;i+=17){fresh(&m,&u,1);m.bytes[BODY+i]=1;CHECK(bridge_update_boot(&u)==0);CHECK(bridge_update_allowed(&u));}
 fresh(&m,&u,1);m.fail=1;CHECK(bridge_update_boot(&u)==BRIDGE_E_IO);CHECK(!u.boot_latched);CHECK(!bridge_update_allowed(&u));CHECK(!bridge_execution_allowed(&u));
 ++cases;
}
static void fault_sweep(void) {
 unsigned char b[IMAGE];image(b,sizeof b);mock base;bridge_update u;
 fresh(&base,&u,0);CHECK(bridge_update_boot(&u)==0);base.calls=0;
 CHECK(upload(&u,b,IMAGE,123)==0);size_t total=base.calls;
 unsigned counts[256]={0};unsigned late=0;
 for(unsigned partial=0;partial<2;partial++) for(size_t fail=1;fail<=total;fail++) {
  mock m;fresh(&m,&u,0);CHECK(bridge_update_boot(&u)==0);m.calls=0;m.fail=fail;m.partial=(int)partial;
  CHECK(upload(&u,b,IMAGE,123)==BRIDGE_E_IO);CHECK(m.calls==fail);
  ++counts[(unsigned char)base.trace[fail-1]];++injections;
  CHECK(bridge_update_finish(&u,b)==BRIDGE_E_STATE);
  CHECK(!bridge_execution_allowed(&u));
  int committed=is_commit(&m);
  if(committed){++late;CHECK(memcmp(m.bytes,b,IMAGE)==0);CHECK(base.trace[fail-1]=='r'||base.trace[fail-1]=='s');}
  else CHECK(!u.committed_this_boot);
  reboot(&m,&u);CHECK(bridge_execution_allowed(&u)==(committed!=0));CHECK(bridge_update_allowed(&u)==(committed==0));++cases;
 }
 CHECK(counts['e']==2);CHECK(counts['E']==2);CHECK(counts['w']>0);CHECK(counts['f']==2*FOOT/16);CHECK(counts['c']==2);CHECK(counts['h']==2);CHECK(counts['s']==4);CHECK(counts['r']>=12);
 printf("Fault sweep: %zu callback indices; erase-footer=%u erase-body=%u body-program=%u footer-program=%u read=%u format=%u hash=%u sync=%u; postcommit errors=%u\n",total,counts['e'],counts['E'],counts['w'],counts['f'],counts['r'],counts['c'],counts['h'],counts['s'],late);
}
static void admission_and_arguments(void) {
 unsigned char b[IMAGE],h[32];image(b,sizeof b);digest(b,sizeof b,h);
 for(unsigned kind=0;kind<7;kind++) {
  mock m;bridge_update u;fresh(&m,&u,0);CHECK(bridge_update_boot(&u)==0);CHECK(bridge_update_begin(&u,IMAGE)==0);
  if(kind<4){int e=bridge_update_write(&u,kind==0?1:0,kind==1?NULL:b,kind==2?0:kind==3?IMAGE+1:16);CHECK(e==BRIDGE_E_ARGUMENT);CHECK(bridge_update_write(&u,0,b,16)==BRIDGE_E_STATE);}
  else {CHECK(bridge_update_write(&u,0,b,IMAGE)==0);if(kind==4)h[0]^=1;if(kind==5)m.format_error=BRIDGE_E_FORMAT;if(kind==6)m.bytes[BODY]=0;
   CHECK(bridge_update_finish(&u,h)==(kind==4?BRIDGE_E_HASH:kind==5?BRIDGE_E_FORMAT:BRIDGE_E_STATE));if(kind==4)h[0]^=1;
  }
  CHECK(m.footer_programs==0);CHECK(bridge_update_finish(&u,h)==BRIDGE_E_STATE);reboot(&m,&u);CHECK(bridge_update_allowed(&u));++cases;
 }
 mock m;bridge_update u;fresh(&m,&u,0);CHECK(bridge_update_boot(&u)==0);
 size_t c=m.calls;CHECK(bridge_update_begin(&u,0)==BRIDGE_E_SIZE);CHECK(bridge_update_begin(&u,15)==BRIDGE_E_SIZE);CHECK(bridge_update_begin(&u,BODY+1)==BRIDGE_E_SIZE);CHECK(m.calls==c);
 CHECK(bridge_update_begin(&u,IMAGE)==0);CHECK(bridge_update_begin(&u,IMAGE)==BRIDGE_E_STATE);CHECK(bridge_update_finish(&u,h)==BRIDGE_E_STATE);
 CHECK(bridge_update_write(&u,0,b,IMAGE)==0);CHECK(bridge_update_finish(&u,NULL)==BRIDGE_E_STATE);CHECK(bridge_update_finish(&u,h)==0);
 CHECK(bridge_update_init(NULL,&ops,&m,FOOT,16)==BRIDGE_E_ARGUMENT);CHECK(bridge_update_init(&u,NULL,&m,FOOT,16)==BRIDGE_E_ARGUMENT);
 CHECK(bridge_update_boot(NULL)==BRIDGE_E_ARGUMENT);CHECK(bridge_update_invalidate(NULL)==BRIDGE_E_ARGUMENT);CHECK(bridge_footer_verify(NULL,0)==BRIDGE_E_ARGUMENT);
 CHECK(bridge_update_begin(NULL,16)==BRIDGE_E_STATE);CHECK(bridge_update_write(NULL,0,b,1)==BRIDGE_E_STATE);CHECK(bridge_update_finish(NULL,h)==BRIDGE_E_STATE);CHECK(!bridge_update_allowed(NULL));CHECK(!bridge_execution_allowed(NULL));++cases;
}
static void invalidate_faults_and_boot_reads(void) {
 /* Invalidation must never erase body or revoke this boot's execution latch,
  * including when the erase or either readback callback fails. */
 for(size_t fail=1;fail<=3;fail++) {
  mock m;bridge_update u;fresh(&m,&u,1);CHECK(bridge_update_boot(&u)==0);
  m.calls=0;m.fail=fail;CHECK(bridge_update_invalidate(&u)==BRIDGE_E_IO);
  CHECK(m.calls==fail);for(size_t p=0;p<BODY;p++)CHECK(m.bytes[p]==0xa5);
  CHECK(bridge_execution_allowed(&u));CHECK(!bridge_update_allowed(&u));
  CHECK(bridge_update_begin(&u,IMAGE)==BRIDGE_E_STATE);
  reboot(&m,&u);CHECK(bridge_update_allowed(&u)==(fail!=1));++cases;++injections;
 }
 for(size_t fail=1;fail<=2;fail++) {
  mock m;bridge_update u;fresh(&m,&u,1);m.fail=fail;
  CHECK(bridge_update_boot(&u)==BRIDGE_E_IO);CHECK(!u.boot_latched);
  CHECK(!bridge_execution_allowed(&u));CHECK(!bridge_update_allowed(&u));
  m.fail=0;CHECK(bridge_update_boot(&u)==0);CHECK(bridge_execution_allowed(&u));++cases;++injections;
 }
 /* A read callback's state error is NOT evidence of an uncommitted footer.
  * Exercise every public error at both chunks, then retry a successful boot. */
 for(int error=BRIDGE_E_ARGUMENT;error>=BRIDGE_E_NOT_FOUND;--error) {
  for(size_t chunk=1;chunk<=2;++chunk) {
   mock failed;bridge_update denied;fresh(&failed,&denied,1);
   failed.fail=chunk;failed.read_error=error;
   CHECK(bridge_update_boot(&denied)==error);CHECK(!denied.boot_latched);
   CHECK(!bridge_update_allowed(&denied));CHECK(!bridge_execution_allowed(&denied));
   failed.fail=0;CHECK(bridge_update_boot(&denied)==BRIDGE_OK);
   CHECK(bridge_execution_allowed(&denied));CHECK(!bridge_update_allowed(&denied));
   ++cases;++injections;
  }
 }
 /* A successful boot latch must not perform storage I/O a second time. */
 mock m;bridge_update u;fresh(&m,&u,0);CHECK(bridge_update_boot(&u)==0);
 size_t before=m.calls;m.fail=before+1;CHECK(bridge_update_boot(&u)==0);CHECK(m.calls==before);++cases;
}
static void silent_program_failures(void) {
 unsigned char b[IMAGE];image(b,sizeof b);
 for(int kind=1;kind<=2;++kind) {
  mock m;bridge_update u;fresh(&m,&u,0);CHECK(bridge_update_boot(&u)==0);
  m.drop_program=kind;
  CHECK(upload(&u,b,IMAGE,31)==(kind==1?BRIDGE_E_HASH:BRIDGE_E_STATE));
  CHECK(u.failed);CHECK(!is_commit(&m));CHECK(!bridge_execution_allowed(&u));
  CHECK(!u.committed_this_boot);if(kind==1)CHECK(m.footer_programs==0);
  reboot(&m,&u);CHECK(bridge_update_allowed(&u));CHECK(!bridge_execution_allowed(&u));++cases;
 }
}
static void geometries(void) {
 mock m;bridge_update u;
 for(unsigned k=0;k<14;k++) {
  bridge_storage_ops o=ops;size_t f=FOOT,min=16;
  switch(k){case 0:o.size=0;break;case 1:o.erase_unit=0;break;case 2:o.write_unit=0;break;case 3:o.write_unit=257;break;case 4:f=0;break;case 5:f=SIZE;break;case 6:o.size=SIZE-1;break;case 7:f=FOOT-1;break;case 8:o.write_unit=17;break;case 9:min=0;break;case 10:min=BODY+1;break;case 11:o.read=NULL;break;case 12:o.erase=NULL;break;case 13:o.program=NULL;break;}
  CHECK(bridge_update_init(&u,&o,&m,f,min)==(k>=11?BRIDGE_E_ARGUMENT:BRIDGE_E_SIZE));++cases;
 }
 bridge_storage_ops o=ops;o.sha256=NULL;CHECK(bridge_update_init(&u,&o,&m,FOOT,16)==BRIDGE_E_ARGUMENT);o=ops;o.check_image=NULL;CHECK(bridge_update_init(&u,&o,&m,FOOT,16)==BRIDGE_E_ARGUMENT);
 unsigned char b[IMAGE];image(b,sizeof b);fresh(&m,&u,0);o=ops;o.sync=NULL;CHECK(bridge_update_init(&u,&o,&m,FOOT,16)==0);CHECK(bridge_update_boot(&u)==0);CHECK(upload(&u,b,IMAGE,9)==0);++cases;
}
int main(void) {
 successful_fragments();boot_and_invalidate();fault_sweep();admission_and_arguments();invalidate_faults_and_boot_reads();silent_program_failures();geometries();
 printf("PASS portable update: %u cases, %u assertions, %u injected callback failures (mock digest; not cryptographic validation)\n",cases,checks,injections);return 0;
}
