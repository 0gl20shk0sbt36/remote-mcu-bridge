"""Native execution of production ABI4 supervisor and module runtime.

Only platform APIs, RISC-V instruction synchronization and entry dispatch are
shimmed. Flash admission/update, worker, SJ_CALL and management code stay real.
No hardware, watchdog timing or instruction emulation is claimed.
"""

from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

HEADERS = {
    "esp_err.h": """#pragma once
#include <stdint.h>
#include <stddef.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NOT_FOUND -2
#define ESP_ERR_INVALID_SIZE -3
#define ESP_ERR_INVALID_STATE -4
#define ESP_ERR_INVALID_ARG -5
#define ESP_ERR_INVALID_VERSION -6
#define ESP_ERR_INVALID_CRC -7
""",
    "esp_attr.h": "#pragma once\n#define RTC_NOINIT_ATTR\n",
    "esp_system.h": "#pragma once\n",
    "esp_task_wdt.h": "#pragma once\n",
    "freertos/FreeRTOS.h": "#pragma once\n",
    "freertos/portmacro.h": "#pragma once\n",
    "esp_partition.h": """#pragma once
#include "esp_err.h"
typedef struct {uint32_t size,address;} esp_partition_t;
typedef unsigned esp_partition_mmap_handle_t;
#define ESP_PARTITION_TYPE_DATA 1
#define ESP_PARTITION_MMAP_INST 1
""",
    "esp_app_desc.h": """#pragma once
typedef struct {uint8_t app_elf_sha256[32];} esp_app_desc_t;
""",
    "native_mbedtls_config.h": "#define MBEDTLS_SHA256_C\n",
}

PRE = r"""
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <time.h>
#include <stdatomic.h>
#include "cJSON.h"
#include "module_runtime.h"
#include "esp_partition.h"
#include "esp_app_desc.h"
#define CONFIG_RMB_TOKEN "native-supervisor-test-token"
#define ESP_LOGE(...) ((void)0)
#define ESP_ERROR_CHECK(x) assert((x)==0)
#define HEADER_US 100000LL
#define UPLOAD_US 100000LL
#define REQUEST_MAX 1024
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
#define portMAX_DELAY -1
#define portMUX_TYPE pthread_mutex_t
#define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
#define portENTER_CRITICAL(x) pthread_mutex_lock(x)
#define portEXIT_CRITICAL(x) pthread_mutex_unlock(x)
typedef int TaskHandle_t;
typedef int QueueHandle_t;
typedef struct {pthread_mutex_t lock;pthread_cond_t changed;bool set;} native_sem;
typedef native_sem *SemaphoreHandle_t;
static pthread_mutex_t qlock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed=PTHREAD_COND_INITIALIZER;
static void *queued;
static bool reject_queue,entered,hang_entry;
static atomic_int feeds;
static _Thread_local int current_task;
static int xTaskGetCurrentTaskHandle(void) {return current_task;}
static int64_t esp_timer_get_time(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (int64_t)t.tv_sec*1000000+t.tv_nsec/1000;}
static int esp_task_wdt_add(void *p) {(void)p;return 0;}
static int esp_task_wdt_reset(void) {atomic_fetch_add(&feeds,1);return 0;}
static void vTaskDelay(int n) {assert(n==100); /* Native restart returns to verify ordering. */}
/* Port task teardown: the native worker returns instead of vanishing. */
static void vTaskDelete(void *t) {(void)t;}
static int esp_reset_reason(void) {return 0;}
static int restarted,sequence,invalidated_at,restarted_at;
static void esp_restart(void) {restarted++;restarted_at=++sequence;}
static SemaphoreHandle_t xSemaphoreCreateBinary(void) {
 native_sem *s=calloc(1,sizeof *s);assert(s);assert(!pthread_mutex_init(&s->lock,NULL));assert(!pthread_cond_init(&s->changed,NULL));return s;
}
static void xSemaphoreGive(native_sem *s) {pthread_mutex_lock(&s->lock);s->set=true;pthread_cond_signal(&s->changed);pthread_mutex_unlock(&s->lock);}
static void xSemaphoreTake(native_sem *s,int ticks) {assert(ticks==portMAX_DELAY);pthread_mutex_lock(&s->lock);while(!s->set)pthread_cond_wait(&s->changed,&s->lock);pthread_mutex_unlock(&s->lock);}
static void vSemaphoreDelete(native_sem *s) {pthread_mutex_destroy(&s->lock);pthread_cond_destroy(&s->changed);free(s);}
static unsigned char flash[0x100000];
static const esp_partition_t partition={sizeof flash,0x220000};
static int erase_error,write_error;
static unsigned body_writes,body_erases;
static const esp_partition_t *esp_partition_find_first(int a,int b,const char *n) {assert(a==1&&b==0x41&&!strcmp(n,"business"));return &partition;}
static int esp_partition_read(const esp_partition_t *p,size_t o,void *d,size_t n) {assert(p==&partition&&o+n<=sizeof flash);memcpy(d,flash+o,n);return 0;}
static int esp_partition_erase_range(const esp_partition_t *p,size_t o,size_t n) {assert(p==&partition&&o+n<=sizeof flash&&!(o%4096)&&!(n%4096));if(erase_error)return -1;if(o<sizeof flash-4096)body_erases++;else invalidated_at=++sequence;memset(flash+o,255,n);return 0;}
static int esp_partition_write(const esp_partition_t *p,size_t o,const void *v,size_t n) {assert(p==&partition&&o+n<=sizeof flash);if(write_error)return -1;if(o<sizeof flash-4096)body_writes++;const uint8_t *b=v;for(size_t i=0;i<n;i++){assert((flash[o+i]&b[i])==b[i]);flash[o+i]&=b[i];}return 0;}
static int esp_partition_mmap(const esp_partition_t *p,size_t o,size_t n,int mode,const void **v,unsigned *h) {assert(p==&partition&&!o&&n==sizeof flash&&mode==1);*v=(void *)(uintptr_t)0x42200000;*h=1;return 0;}
static void esp_partition_munmap(unsigned h) {assert(h==1);}
static const esp_app_desc_t *esp_app_get_description(void) {static const esp_app_desc_t a={{0}};return &a;}
static int native_module_entry(const rmb_host *h) {
 assert(h->abi==4&&h->size==sizeof *h);
 pthread_mutex_lock(&qlock);entered=true;pthread_cond_broadcast(&changed);
 while(hang_entry)pthread_cond_wait(&changed,&qlock);
 pthread_mutex_unlock(&qlock);return 0;
}
static bool pending_fault;
static void service_fault_status(int fd);
static bool service_fault_ack(uint32_t seq) {if(!pending_fault||seq!=7)return false;pending_fault=false;return true;}
static void service_supervisor_fault(uint32_t reason,bool entry,uint32_t checkpoint,int64_t began,int64_t progress,int64_t limit,int64_t now) {(void)reason;(void)entry;(void)checkpoint;(void)began;(void)progress;(void)limit;(void)now;pending_fault=true;}
static bool service_watchdog_begin_init(void) {return true;}
static void service_watchdog_finish_init(void) {}
static void service_watchdog_abort_init(void) {}
static bool service_fault_pending(void) {return pending_fault;}
void rmb_network_init_begin(void) {}
bool rmb_network_init_commit(void) {return true;}
void rmb_network_init_abort(void) {}
bool module_watchdog_configured(void) {return true;}
void module_host_configure(rmb_host *h) {(void)h;}
#include "rmb/port.h"
#include "mbedtls/sha256.h"
#define TAG "native-supervisor"
#ifndef RMB_CONFIG_TOKEN
#define RMB_CONFIG_TOKEN CONFIG_RMB_TOKEN
#endif

/* Extra mocks the port contract needs beyond this fixture's own seams. */
void *xSemaphoreCreateMutex(void){static int lock;return &lock;}
void *xQueueCreate(unsigned l,unsigned i){(void)l;(void)i;static int q;return &q;}
int xTaskCreate(void (*f)(void *),const char *n,unsigned s,void *a,int p,void **o){(void)f;(void)n;(void)s;(void)a;(void)p;if(o)*o=(void *)2;return 1;}
void xTaskNotifyGive(void *t){(void)t;}
unsigned ulTaskNotifyTake(int c,unsigned t){(void)c;(void)t;return 0;}
unsigned uxTaskGetStackHighWaterMark(void *t){(void)t;return 1024;}
int esp_task_wdt_reconfigure(const void *c){(void)c;return 0;}
int xPortInIsrContext(void){return 0;}
/* Port contract over this fixture's seams. */
void rmb_port_log(int level,const char *tag,const char *format,...){(void)level;(void)tag;(void)format;}
int64_t rmb_port_now_us(void){return esp_timer_get_time();}
void rmb_port_delay_ticks(uint32_t t){vTaskDelay(t);}
void rmb_port_restart(void){esp_restart();}
rmb_reset_reason rmb_port_reset_reason(void){return (rmb_reset_reason)esp_reset_reason();}
bool rmb_port_in_isr(void){return xPortInIsrContext()!=0;}
void rmb_port_lock_enter(rmb_lock_t *p){pthread_mutex_lock(p);}
void rmb_port_lock_exit(rmb_lock_t *p){pthread_mutex_unlock(p);}
int rmb_port_task_create(const char *n,void (*f)(void *),void *c,uint32_t s,int p,rmb_task_t *o){return xTaskCreate(f,n,s,c,p,o)==1?0:-1;}
/* The mock task handle is an int id; the port handle is opaque. */
rmb_task_t rmb_port_task_current(void){return (rmb_task_t)(uintptr_t)xTaskGetCurrentTaskHandle();}
void rmb_port_task_end(void){vTaskDelete(NULL);}
void rmb_port_task_terminate(rmb_task_t t){vTaskDelete(t);}
void rmb_port_task_wait_notify(void){(void)ulTaskNotifyTake(1,0xffffffffu);}
void rmb_port_task_notify(rmb_task_t t){xTaskNotifyGive(t);}
uint32_t rmb_port_task_stack_min_free(void){return uxTaskGetStackHighWaterMark(NULL);}
/* Mutexes are pthread mutexes here; the fixture's int-sentinel mock cannot
 * support blocking take. Locking is never timed in the resident. */
int rmb_port_mutex_create(rmb_mutex_t *o){static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;*o=&lock;return 0;}
int rmb_port_mutex_take(rmb_mutex_t m,uint32_t t){(void)t;return pthread_mutex_lock((pthread_mutex_t *)m);}
void rmb_port_mutex_give(rmb_mutex_t m){pthread_mutex_unlock((pthread_mutex_t *)m);}
void rmb_port_mutex_delete(rmb_mutex_t m){(void)m;}
int rmb_port_sem_create(rmb_sem_t *o){*o=xSemaphoreCreateBinary();return *o?0:-1;}
/* xSemaphoreTake is void-returning and asserts an infinite wait, which is the
 * only way the resident takes a semaphore (release signals the submitter). */
int rmb_port_sem_take(rmb_sem_t s,uint32_t t){xSemaphoreTake(s,(int)t);return 0;}
void rmb_port_sem_give(rmb_sem_t s){xSemaphoreGive(s);}
void rmb_port_sem_delete(rmb_sem_t s){vSemaphoreDelete(s);}
int rmb_port_queue_create(uint32_t l,uint32_t i,rmb_queue_t *o){*o=xQueueCreate(l,i);return *o?0:-1;}
/* Queue transfer is defined with the supervisor_job-typed queue below. */
int rmb_port_wdt_register_current(void){return 0;}
int rmb_port_wdt_feed(void){return 0;}
int rmb_port_wdt_configure(const rmb_wdt_config_t *c){(void)c;return 0;}
void *rmb_port_retained(size_t b,size_t a){(void)b;(void)a;return NULL;}
/* Storage seam over the fixture's fake flash. */
int rmb_port_slot_geometry(uint32_t *size,uint32_t *footer){*size=sizeof flash;*footer=4096;return 0;}
int rmb_port_slot_read(uint32_t o,void *d,uint32_t n){return esp_partition_read(&partition,o,d,n)?-1:0;}
int rmb_port_slot_erase(uint32_t o,uint32_t n){return esp_partition_erase_range(&partition,o,n)?-1:0;}
int rmb_port_slot_write(uint32_t o,const void *v,uint32_t n){return esp_partition_write(&partition,o,v,n)?-1:0;}
int rmb_port_slot_sha256(uint32_t o,uint32_t n,uint8_t digest[32]){mbedtls_sha256_context c;mbedtls_sha256_init(&c);int rc=mbedtls_sha256_starts(&c,0);uint8_t b[512];while(!rc&&n){uint32_t k=n>sizeof b?sizeof b:n;if(rmb_port_slot_read(o,b,k)){mbedtls_sha256_free(&c);return -1;}rc=mbedtls_sha256_update(&c,b,k);o+=k;n-=k;}if(!rc)rc=mbedtls_sha256_finish(&c,digest);mbedtls_sha256_free(&c);return rc?-1:0;}
int rmb_port_slot_map(uint32_t *text){*text=(uint32_t)0x42200000;return 0;}
int rmb_port_slot_unmap(void){return 0;}
int rmb_port_base_sha256(uint8_t digest[32]){memset(digest,0,32);return 0;}
"""

QUEUE = r"""
static int xQueueSend(int q,supervisor_job **p,int ticks) {
 (void)q;(void)ticks;pthread_mutex_lock(&qlock);
 if(queued||reject_queue){pthread_mutex_unlock(&qlock);return 0;}
 queued=*p;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&qlock);return pdTRUE;
}
static int xQueueReceive(int q,supervisor_job **p,int ticks) {
 (void)q;(void)ticks;pthread_mutex_lock(&qlock);
 while(!queued)pthread_cond_wait(&changed,&qlock);
 *p=queued;queued=NULL;pthread_mutex_unlock(&qlock);return pdTRUE;
}
/* Port queue transfers ride the same single-slot job queue; the item payload is
 * one supervisor_job pointer, which is exactly what the fixture queues. */
int rmb_port_queue_send(rmb_queue_t q,const void *i,uint32_t t) {
 return xQueueSend((int)(intptr_t)q,(supervisor_job **)(uintptr_t)i,(int)t)?0:-1;
}
int rmb_port_queue_receive(rmb_queue_t q,void *i,uint32_t t) {
 return xQueueReceive((int)(intptr_t)q,(supervisor_job **)i,(int)t)?0:-1;
}
"""

SUPPORT = r"""
bool module_service_available(void) {return !supervisor_fault;}
static void service_fault_status(int fd) {reply(fd,pending_fault?"{\"ok\":true,\"pending\":true}\n":"{\"ok\":true,\"pending\":false}\n");}
static void *thread(void *arg) {current_task=1;supervisor_worker(arg);return NULL;}
static void reset_boot(bool committed) {
 memset(flash,255,sizeof flash);if(committed)memset(flash+sizeof flash-4096,0,4096);
 mapped_base=0;data_reserve=0;memset(&update,0,sizeof update);attempted=false;
 atomic_store(&admitted,false);busy=installing=slot_invalid=false;
 supervisor_entry=(rmb_supervisor_entry){0};supervisor_fault=supervisor_active=supervisor_updating=supervisor_reboot_required=false;
 assert(module_runtime_prepare()==0);
 rmb_supervisor_entry_capture(&supervisor_entry,!module_runtime_update_allowed());
 supervisor_queue=(rmb_queue_t)(uintptr_t)1;supervisor_worker_handle=(rmb_task_t)(uintptr_t)1;
}
static void image(uint8_t out[76]) {
 module_header h={MODULE_MAGIC,4,sizeof h,76,0x42200028,4,(uint32_t)(uintptr_t)module_data_reserve,32,4,0x42200028};
 memcpy(out,&h,sizeof h);memset(out+sizeof h,0,36);
}
"""


def build_run(post):
    """Compile live production sources with explicit, checked native seams."""
    import sys

    sys.path.insert(0, str(ROOT.parent / "tools"))
    from native_deps import cjson

    cj = cjson()
    mbed = Path(os.environ["IDF_PATH"]) / "components/mbedtls/mbedtls"
    source = (ROOT / "main/supervisor.inc").read_text()
    service = (ROOT / "main/resident.c").read_text()
    runtime = (ROOT / "main/module_runtime.c").read_text()
    # Native CPUs cannot execute RISC-V fence.i or the uploaded entry address.
    fence = '__asm__ volatile("fence.i" ::: "memory")'
    entry = "((module_entry_fn)(uintptr_t)h.entry)(&host)"
    assert runtime.count(fence) == runtime.count(entry) == 1
    runtime = runtime.replace(fence, "atomic_thread_fence(memory_order_seq_cst)")
    runtime = runtime.replace(entry, "native_module_entry(&host)")
    helpers = service[
        service.index("bool authenticated(") : service.index('#include "streams.inc"')
    ]
    types = source[
        source.index("typedef rmb_supervisor_record") : source.index(
            "static void supervisor_record_write_locked("
        )
    ]
    implementation = source[
        source.index("static void supervisor_record_write_locked(") : source.index(
            "static void supervisor_management("
        )
    ]
    # Unused monitor declaration belongs to the deliberately excluded IDF task.
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
        p = Path(d)
        for name, text in HEADERS.items():
            target = p / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        (p / "native.c").write_text(
            PRE
            + runtime
            + helpers
            + '\n#include "rmb/supervisor.h"\n#define SUPERVISOR_JOB_US (1800LL*1000000)\n'
            + types
            + QUEUE
            + implementation
            + SUPPORT
            + post
        )
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-pointer-to-int-cast",
                '-DRMB_PORT_PLATFORM_HEADER="port_mock_platform.h"',
                "-I"
                + str(
                    next(
                        p
                        for p in (
                            ROOT / "tests",
                            ROOT / "esp32_rom" / "tests",
                            ROOT.parent / "tests",
                        )
                        if (p / "port_native.h").is_file()
                    )
                ),
                "-Wno-unused-function",
                "-fno-pie",
                "-no-pie",
                "-pthread",
                "-fsanitize=undefined",
                '-DMBEDTLS_CONFIG_FILE="native_mbedtls_config.h"',
                "-I" + str(mbed / "include"),
                "-fno-sanitize-recover=all",
                "-I" + str(p),
                "-I" + str(cj),
                "-I" + str(ROOT / "main"),
                "-I" + str(ROOT.parent / "include"),
                str(p / "native.c"),
                str(ROOT.parent / "core/update.c"),
                str(ROOT.parent / "core/supervisor.c"),
                str(cj / "cJSON.c"),
                str(mbed / "library/sha256.c"),
                str(mbed / "library/platform_util.c"),
                "-lm",
                "-o",
                str(p / "native"),
            ],
            check=True,
        )
        subprocess.run([str(p / "native")], check=True, timeout=10)


POST = r"""
static int __attribute__((aligned(4))) job(void *p) {assert(current_task==1);assert(supervisor_run(job,p,1)==-1);supervisor_progress();return ++*(int *)p;}
int main(void) {
 reset_boot(false);assert(supervisor_start()==-1);
 uint8_t body[76];image(body);uint8_t digest[32];
 assert(!mbedtls_sha256(body,sizeof body,digest,0));
 assert(!module_runtime_install_begin(sizeof body));assert(!module_runtime_install_write(0,body,sizeof body));assert(!module_runtime_install_finish(digest));
 assert(!module_runtime_update_allowed()&&!module_runtime_ready()&&!entered);
 /* A real reset is required before any entry, even with a committed footer. */
 mapped_base=0;data_reserve=0;memset(&update,0,sizeof update);assert(!module_runtime_prepare());
 supervisor_entry=(rmb_supervisor_entry){0};rmb_supervisor_entry_capture(&supervisor_entry,true);
 pthread_t t;assert(!pthread_create(&t,NULL,thread,NULL));
 reject_queue=true;assert(supervisor_start()==-1);assert(!supervisor_init_submitted&&!supervisor_active&&!supervisor_rtc.active);reject_queue=false;
 /* First exercise the generic synchronous SJ_CALL path and borrowed context. */
 assert(!module_runtime_init());int value=40;
 /* Native callback provenance uses actual production range validation. */
 admitted_header.text_addr=(uint32_t)(uintptr_t)job;
 admitted_header.text_size=4;
 /* The native binary is non-PIE so callback/data addresses fit ABI4 uint32_t;
  * only the admitted native callback range is substituted after real entry. */
 assert(supervisor_run(NULL,&value,1)==-1);
 assert(supervisor_run(job,&value,0)==-1);
 assert(supervisor_run(job,&value,SUPERVISOR_JOB_US+1)==-1);
 assert(module_runtime_code_pointer((const void *)job));
 assert(supervisor_run(job,&value,123456)==41&&value==41&&!supervisor_active);
 assert(supervisor_stage==1&&supervisor_limit_us==123456&&rmb_supervisor_record_valid(&supervisor_rtc));
 /* New boot: INIT deliberately blocks in the explicit native entry shim. */
 attempted=false;atomic_store(&admitted,false);entered=false;hang_entry=true;
 supervisor_entry=(rmb_supervisor_entry){.captured=true,.allowed=true};
 assert(!supervisor_start());pthread_mutex_lock(&qlock);while(!entered)pthread_cond_wait(&changed,&qlock);pthread_mutex_unlock(&qlock);
 assert(supervisor_init_submitted&&supervisor_active&&supervisor_rtc.active&&rmb_supervisor_record_valid(&supervisor_rtc));
 int before=atomic_load(&feeds);assert(supervisor_start()==-1);
 int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));
 for(unsigned i=0;i<20;i++) {char out[1024];const char *request="{\"token\":\"native-supervisor-test-token\",\"op\":\"status\"}\n";assert(write(f[0],request,strlen(request))==(ssize_t)strlen(request));supervisor_serve(f[1]);ssize_t got=read(f[0],out,sizeof out-1);assert(got>0);out[got]=0;
 assert(strstr(out,"\"supervisor\":17")&&strstr(out,"\"module_abi\":4")&&strstr(out,"\"busy\":true")&&strstr(out,"\"module_ready\":false")&&strstr(out,"\"update_mode\":false"));
 assert(strstr(out,"\"text_address\":1109393448"));
 char expected[80];snprintf(expected,sizeof expected,"\"data_address\":%lu",(unsigned long)module_runtime_data_address());assert(strstr(out,expected));
 }
 assert(atomic_load(&feeds)==before);close(f[0]);close(f[1]);
 puts("PASS production ABI4 runtime + supervisor: real commit/admission, generic SJ_CALL/context/progress, hung async INIT, retained RTC CRC, 20 management replies, no worker feed");return 0;
}
"""

if __name__ == "__main__":
    build_run(POST)
