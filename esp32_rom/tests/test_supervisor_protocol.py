"""Execute production management parser/transaction against real cJSON and sockets.
Runtime/FreeRTOS/partition operations are deterministic host mocks, not hardware.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT.parent / 'tools'))
from native_deps import cjson
CJSON = cjson()
service = (ROOT / 'main/service.c').read_text()
supervisor = (ROOT / 'main/supervisor.inc').read_text()
helpers = service[service.index('bool authenticated('):service.index('#include "module_runtime.h"')]
parser = supervisor[supervisor.index('static bool supervisor_hex('):supervisor.index('static void supervisor_management(')]
pre = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <errno.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <time.h>
#include "cJSON.h"
#define CONFIG_ROM_SERVICE_TOKEN "native-supervisor-test-token"
#define ESP_LOGE(...) ((void)0)
#define HEADER_US 100000LL
#define UPLOAD_US 100000LL
#define REQUEST_MAX 1024
#define ESP_PARTITION_TYPE_DATA 1
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
#define SJ_INIT 0
#define SJ_HANG 1
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
typedef struct {uint32_t size;} esp_partition_t;
typedef struct {int kind,done;} supervisor_job;
static int supervisor_lock,supervisor_queue;
static bool supervisor_fault,supervisor_active,supervisor_updating,supervisor_reboot_required;
static uint32_t supervisor_uploaded;
static int64_t supervisor_started,supervisor_last_progress,supervisor_limit_us;
static uint32_t supervisor_stage;
static bool supervisor_init_submitted;
static bool module_allowed_this_boot=true,update_allowed;
static bool module_runtime_update_allowed(void) {return update_allowed;}
static int invalidate_error,sequence,invalidated_at,restarted_at;
static int writes,begins,finishes,invalidates,reserved,available=1,busy,begin_error,write_error,finish_error,restarted;
static uint32_t written;
static int64_t esp_timer_get_time(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (int64_t)t.tv_sec*1000000+t.tv_nsec/1000;}
static const esp_partition_t *esp_partition_find_first(int a,int b,const char *name) {static esp_partition_t p={0x100000};assert(a==1&&b==0x41&&!strcmp(name,"business"));return &p;}
static bool control_acquire(void) {if(reserved)return false;reserved=1;return true;}
static bool module_runtime_busy(void) {return busy;}
static bool module_runtime_ready(void) {return available;}
static int module_runtime_install_begin(uint32_t n) {assert(reserved&&n);begins++;written=0;return begin_error;}
static int module_runtime_install_write(uint32_t off,const void *p,size_t n) {assert(reserved&&off==written&&p&&n);writes++;written+=(uint32_t)n;return write_error;}
static int module_runtime_install_finish(const uint8_t h[32]) {assert(reserved&&h[0]==0x12);finishes++;return finish_error;}
static int module_runtime_invalidate(void) {invalidates++;invalidated_at=++sequence;return invalidate_error;}
static void supervisor_record_write_locked(void) {}
static int starts;
static int supervisor_start(void) {starts++;supervisor_init_submitted=true;supervisor_active=true;return 0;}
static void supervisor_status(int fd);
static void esp_restart(void) {restarted++;restarted_at=++sequence;}
static void vTaskDelay(int n) {(void)n;}

static int xQueueSend(int q,supervisor_job **j,int t) {(void)q;(void)j;(void)t;return 1;}
'''
post = r'''
static void supervisor_status(int fd) {reply(fd,"{\"ok\":true,\"fault\":false}\n");}
static char result[4096];
static void run(const char *text,const void *payload,size_t n) {
 int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));
 assert(write(f[0],text,strlen(text))==(ssize_t)strlen(text));
 if(n)assert(write(f[0],payload,n)==(ssize_t)n);
 shutdown(f[0],SHUT_WR);
 bool owned=false;supervisor_serve(f[1],&owned);
 if(owned)reserved=0;
 shutdown(f[1],SHUT_WR);
 ssize_t got=read(f[0],result,sizeof(result)-1);assert(got>=0);result[got]=0;
 close(f[0]);close(f[1]);
}
#define TOKEN "\"token\":\"native-supervisor-test-token\""
#define HASH "1212121212121212121212121212121212121212121212121212121212121212"
#define UP "{" TOKEN ",\"op\":\"module-upload\",\"size\":8,\"sha256\":\"" HASH "\"}\n"
int main(void) {
 const char *bad[]={
 "{\"token\":\"wrong\",\"op\":\"module-upload\",\"size\":8,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"status\",\"op\":\"status\"}\n",
 "{" TOKEN ",\"op\":\"status\",\"extra\":0}\n",
 "{" TOKEN ",\"op\":\"status\",\"size\":8}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":8.5,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":0,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":1044481,\"sha256\":\"" HASH "\"}\n",
 "{" TOKEN ",\"op\":\"module-upload\",\"size\":8,\"sha256\":\"g212121212121212121212121212121212121212121212121212121212121212\"}\n",
 "{" TOKEN ",\"op\":\"sta\\u0000tus\"}\n",
 "{" TOKEN ",\"op\":\"status\"}{}\n"
 };
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++) {run(bad[i],NULL,0);assert(strstr(result,"\"ok\":false"));assert(!begins&&!writes&&!finishes&&!reserved);}
 run("{" TOKEN ",\"op\":\"status\"}\n",NULL,0);assert(strstr(result,"\"ok\":true"));
 available=0;run("{" TOKEN ",\"op\":\"status\"}\n",NULL,0);assert(strstr(result,"\"ok\":true"));available=1;
 char long_header[1026];memset(long_header,'a',1024);long_header[1024]='\n';long_header[1025]=0;run(long_header,NULL,0);assert(strstr(result,"\"ok\":false")&&!begins);
 const uint8_t body[8]={0,1,2,3,4,5,0xff,0};
 /* Committed startup must reject body writes even if entry failed/faulted. */
 run(UP,body,8);assert(!begins&&strstr(result,"enter-update"));
 available=0;supervisor_fault=true;run(UP,body,8);assert(!begins&&strstr(result,"enter-update"));
 available=1;supervisor_fault=false;
 update_allowed=true;module_allowed_this_boot=false;
 run("{" TOKEN ",\"op\":\"clear-fault\"}\n",NULL,0);assert(!starts&&strstr(result,"restart ESP"));
 run(UP,body,8);assert(begins==1&&writes==1&&finishes==1&&written==8&&supervisor_uploaded==8&&!supervisor_updating&&!reserved);assert(strstr(result,"\"ready\":true")&&strstr(result,"\"reboot_required\":true")&&!starts&&!restarted);
 run(UP,body,8);assert(begins==1&&strstr(result,"enter-update"));
 supervisor_reboot_required=false; /* Independent simulated update boot for failures below. */
 int previous=begins;reserved=1;run(UP,body,8);assert(begins==previous&&strstr(result,"busy"));reserved=0;
 busy=1;run(UP,body,8);assert(begins==previous&&strstr(result,"busy")&&!reserved);busy=0;
 run(UP,body,3);assert(invalidates==1&&!supervisor_updating&&!reserved&&strstr(result,"disconnect"));
 write_error=1;run(UP,body,8);assert(invalidates==2&&!supervisor_updating&&!reserved);write_error=0;
 finish_error=1;run(UP,body,8);assert(invalidates==3&&!supervisor_updating&&!reserved);finish_error=0;
 supervisor_fault=true;supervisor_stage=42;supervisor_limit_us=99;run(UP,body,8);assert(!supervisor_fault&&!supervisor_active&&!supervisor_stage&&!supervisor_limit_us&&!reserved&&!starts&&!restarted);
 const char *danger[]={"esp-restart","clear-fault","module-test-hang","enter-update"};
 for(unsigned i=0;i<4;i++){char h[160];snprintf(h,sizeof h,"{\"token\":\"wrong\",\"op\":\"%s\"}\n",danger[i]);run(h,NULL,0);assert(strstr(result,"\"ok\":false")&&!starts&&!restarted&&!supervisor_active);}
 run("{" TOKEN ",\"op\":\"clear-fault\"}\n",NULL,0);assert(!starts&&strstr(result,"restart ESP"));
 supervisor_reboot_required=false;module_allowed_this_boot=true;update_allowed=false; /* Committed recovery boot. */
 available=0;supervisor_fault=true;run("{" TOKEN ",\"op\":\"clear-fault\"}\n",NULL,0);assert(starts==1&&supervisor_active&&!supervisor_fault&&strstr(result,"\"initializing\":true"));
 run("{" TOKEN ",\"op\":\"status\"}\n",NULL,0);assert(strstr(result,"\"ok\":true"));
 run("{" TOKEN ",\"op\":\"clear-fault\"}\n",NULL,0);assert(starts==1&&strstr(result,"busy"));
 supervisor_active=false;run("{" TOKEN ",\"op\":\"clear-fault\"}\n",NULL,0);assert(starts==1&&strstr(result,"already attempted"));
 run("{" TOKEN ",\"op\":\"esp-restart\"}\n",NULL,0);assert(restarted==1);
 /* Footer failure: no success/restart. Retry works despite persistent UART
  * reservation and busy/hung business worker, with no body writes. */
 int old_begins=begins,old_writes=writes,old_finishes=finishes;
 reserved=1;busy=1;supervisor_active=true;invalidate_error=1;
 run("{" TOKEN ",\"op\":\"enter-update\"}\n",NULL,0);
 assert(restarted==1&&strstr(result,"footer erase/verify failed")&&reserved);
 invalidate_error=0;
 run("{" TOKEN ",\"op\":\"enter-update\"}\n",NULL,0);
 assert(restarted==2&&invalidated_at<restarted_at&&strstr(result,"\"reboot_required\":true")&&reserved);
 assert(begins==old_begins&&writes==old_writes&&finishes==old_finishes);
 /* Mock reset returned: changing current footer must not grant boot permission. */
 reserved=0;busy=0;run(UP,body,8);assert(begins==old_begins&&strstr(result,"enter-update"));
 puts("PASS actual management C: strict auth, reboot-before-update including fault boot, no hot activation, footer failure/retry before reset despite busy UART, binary upload and failure cleanup");
}
'''
with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as d:
    p=Path(d)
    (p/'management.c').write_text(pre+helpers+parser+post)
    cjson=CJSON
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(cjson),str(p/'management.c'),str(cjson/'cJSON.c'),'-lm','-o',str(p/'management')],check=True)
    subprocess.run([str(p/'management')],check=True,timeout=10)
