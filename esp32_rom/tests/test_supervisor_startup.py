"""Execute production async INIT/worker/status with pthread scheduling.
No ESP watchdog or hardware emulation: proves an entry blocked on the worker
cannot retain the management stack or stop management status replies.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / 'main/supervisor.inc').read_text()
types = src[src.index('typedef enum { SJ_INIT'):src.index('static void supervisor_record_write_locked(')]
record = src[src.index('static void supervisor_record_write_locked('):src.index('static void supervisor_progress(')]
worker = src[src.index('static void supervisor_worker('):src.index('static int supervisor_submit(')]
start_status = src[src.index('#define supervisor_init_submitted'):src.index('static bool supervisor_hex(')]
pre = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/socket.h>
#include "rom.h"
#include "rmb/supervisor.h"
#define MODULE_ABI_VERSION 2u
#define SUPERVISOR_MAGIC UINT32_C(0x53555015)
#define SUPERVISOR_JOB_US (1800LL*1000000)
#define ROM_E_CAP -9
#define ESP_ERROR_CHECK(x) assert((x)==0)
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
#define portENTER_CRITICAL(x) pthread_mutex_lock(x)
#define portEXIT_CRITICAL(x) pthread_mutex_unlock(x)
typedef void *SemaphoreHandle_t;
typedef rmb_supervisor_record supervisor_record;
static supervisor_record supervisor_rtc;
static pthread_mutex_t supervisor_lock=PTHREAD_MUTEX_INITIALIZER;
static bool supervisor_active,supervisor_fault,supervisor_updating,supervisor_reboot_required;
static rmb_supervisor_entry supervisor_entry={.captured=true,.allowed=true};
#define module_allowed_this_boot (supervisor_entry.allowed)
static bool module_runtime_update_allowed(void) {return !module_allowed_this_boot;}
static uint32_t supervisor_stage,supervisor_uploaded;
static int64_t supervisor_started,supervisor_last_progress,supervisor_limit_us;
static int supervisor_queue=1;
static const rom_device *device;
static pthread_mutex_t qlock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed=PTHREAD_COND_INITIALIZER;
static void *queued;
static bool entered,reject_queue;
static int feeds;
static int64_t esp_timer_get_time(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (int64_t)t.tv_sec*1000000+t.tv_nsec/1000;}
static int esp_task_wdt_add(void *p) {(void)p;return 0;}
static int esp_task_wdt_reset(void) {feeds++;return 0;}
static void vTaskDelay(int n) {(void)n;assert(!"unexpected hang test");}
static void xSemaphoreGive(void *p) {(void)p;assert(!"INIT must not borrow a semaphore");}
static int module_runtime_init(void) {
 pthread_mutex_lock(&qlock);entered=true;pthread_cond_broadcast(&changed);
 /* Deliberately never complete entry: management must still respond. */
 for(;;) pthread_cond_wait(&changed,&qlock);
 return 0;
}
#define module_rom_validate(...) 0
#define module_rom_download_traced(...) 0
#define module_rom_probe(...) 0
#define module_rom_restart(...) 0
static bool module_runtime_ready(void) {return false;}
static bool module_runtime_busy(void) {return false;}
static uintptr_t module_runtime_text_address(void) {return UINT32_C(0x42012340);}
static uintptr_t module_runtime_data_address(void) {return UINT32_C(0x3fc90000);}
static int esp_reset_reason(void) {return 0;}
static bool reply(int fd,const char *s) {return write(fd,s,strlen(s))==(ssize_t)strlen(s);}
'''
queue = r'''
static int xQueueSend(int q,supervisor_job **p,int ticks) {
 (void)q;(void)ticks;pthread_mutex_lock(&qlock);
 if(queued || reject_queue){pthread_mutex_unlock(&qlock);return 0;}
 queued=*p;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&qlock);return pdTRUE;
}
static int xQueueReceive(int q,supervisor_job **p,int ticks) {
 (void)q;(void)ticks;pthread_mutex_lock(&qlock);
 while(!queued)pthread_cond_wait(&changed,&qlock);
 *p=queued;queued=NULL;pthread_mutex_unlock(&qlock);return pdTRUE;
}
'''
post = r'''
static void *thread(void *arg) {supervisor_worker(arg);return NULL;}
int main(void) {
 (void)device;
 pthread_t t;assert(!pthread_create(&t,NULL,thread,NULL));
 module_allowed_this_boot=false;assert(supervisor_start()==ROM_E_CAP);
 {int f[2];char out[1024];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));
  supervisor_status(f[1]);ssize_t n=read(f[0],out,sizeof out-1);assert(n>0);out[n]=0;
  assert(strstr(out,"\"update_mode\":true")&&strstr(out,"\"module_ready\":false"));
  supervisor_reboot_required=true;supervisor_status(f[1]);n=read(f[0],out,sizeof out-1);assert(n>0);out[n]=0;
  assert(strstr(out,"\"update_mode\":false")&&strstr(out,"\"module_ready\":false"));
  assert(supervisor_start()==ROM_E_CAP);close(f[0]);close(f[1]);supervisor_reboot_required=false;}
 module_allowed_this_boot=true;
 reject_queue=true;assert(supervisor_start()==ROM_E_IO);
 assert(!supervisor_init_submitted && !supervisor_active && !supervisor_rtc.active);
 reject_queue=false;
 assert(supervisor_start()==0); /* Returns WITHOUT waiting on entry. */
 pthread_mutex_lock(&qlock);while(!entered)pthread_cond_wait(&changed,&qlock);pthread_mutex_unlock(&qlock);
 assert(supervisor_init_submitted&&supervisor_active&&supervisor_rtc.active);
 assert(rmb_supervisor_record_valid(&supervisor_rtc));
 assert(feeds==1); /* No automatic worker feed while stuck in entry. */
 assert(supervisor_start()==ROM_E_CAP); /* Static job never overwritten. */
 int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));
 for(unsigned i=0;i<20;i++) {
  supervisor_status(f[1]);char out[1024];ssize_t n=read(f[0],out,sizeof out-1);assert(n>0);out[n]=0;
  assert(strstr(out,"\"supervisor\":17")&&strstr(out,"\"busy\":true")&&strstr(out,"\"module_ready\":false"));
  assert(strstr(out,"\"update_mode\":false"));
  assert(strstr(out,"\"text_address\":1107370816")&&strstr(out,"\"data_address\":1070137344"));
 }
 puts("PASS production async INIT/worker/status: hung entry, retained RTC active+CRC, 20 management replies, one-shot static job, no worker feed");
 return 0;
}
'''
with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as d:
    p = Path(d)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    '-I'+str(ROOT.parent/'include'), '-I'+str(ROOT.parent/'rom'),
                    str(ROOT/'tests/test_supervisor_core.c'),
                    str(ROOT.parent/'core/supervisor.c'), str(ROOT.parent/'rom/rom.c'),
                    '-o', str(p/'core')], check=True)
    subprocess.run([str(p/'core')], check=True, timeout=5)
    (p / 'startup.c').write_text(pre + types + queue + record + worker + start_status + post)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread', '-I'+str(ROOT.parent/'rom'), '-I'+str(ROOT.parent/'include'), str(ROOT.parent/'core/supervisor.c'), str(p/'startup.c'), '-o', str(p/'startup')], check=True)
    subprocess.run([str(p/'startup')], check=True, timeout=5)
