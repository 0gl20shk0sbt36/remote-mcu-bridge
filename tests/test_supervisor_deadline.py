#!/usr/bin/env python3
"""Execute real worker/monitor + RTC journal with deterministic platform seams."""

from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
supervisor = (ROOT / "esp32_rom/main/supervisor.inc").read_text()
types = supervisor[
    supervisor.index("typedef rmb_supervisor_record") : supervisor.index(
        "static void supervisor_record_write_locked("
    )
]
worker = supervisor[
    supervisor.index("static void supervisor_record_write_locked(") : supervisor.index(
        "static int supervisor_submit("
    )
]
monitor = supervisor[
    supervisor.index("static void supervisor_monitor(") : supervisor.index(
        "static void supervisor_init("
    )
]
preamble = r"""
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include <assert.h>
#include <setjmp.h>
#include "rmb/service.h"
#include "port_native.h"
#include "rmb/supervisor.h"
#define RTC_NOINIT_ATTR
#define portMUX_INITIALIZER_UNLOCKED 0
typedef int portMUX_TYPE;
#define ESP_ERROR_CHECK(x) assert((x)==0)
#define ESP_RST_TASK_WDT 6
#define ESP_RST_INT_WDT 5
#define ESP_RST_WDT 7
#define ESP_RST_PANIC 4
static int lock_depth,resets,notifications,reset_reason=1;
static int64_t clock_us;
static jmp_buf reset_trap,finished_trap;
static char response[1024];
static void enter(portMUX_TYPE *p) {(void)p;assert(!lock_depth);++lock_depth;}
static void leave(portMUX_TYPE *p) {(void)p;assert(lock_depth==1);--lock_depth;}
#define portENTER_CRITICAL(p) enter(p)
#define portEXIT_CRITICAL(p) leave(p)
static int xPortInIsrContext(void) {return 0;}
static void reply(int fd,const char *s) {(void)fd;strcpy(response,s);}
"""
platform = r"""
static supervisor_job *pending;
 (void)queue;(void)ticks;
 if(!pending)longjmp(finished_trap,1);
 *out=pending;pending=NULL;return pdTRUE;
}
static int init_result;static int64_t init_delay;
static int module_runtime_init(void) {
 assert(service_watchdog_open("entry-root",60000)>0);
 clock_us+=init_delay;return init_result;
}
"""
post = r"""
static void fresh(void) {
 memset(&lease_rtc,0,sizeof lease_rtc);clock_us=1;resets=notifications=0;reset_reason=1;
 service_watchdog_boot();supervisor_worker_handle=1;supervisor_queue=1;
 supervisor_fault=supervisor_active=supervisor_initializing=false;
 memset(&supervisor_rtc,0,sizeof supervisor_rtc);init_delay=0;init_result=0;
}
static int late(void *context) {(void)context;supervisor_progress();clock_us+=1001;return 0;}
static int timely(void *context) {clock_us+=100;return ++*(int *)context;}
static void run(supervisor_job *job) {
 pending=job;
 if(!setjmp(reset_trap))if(!setjmp(finished_trap))supervisor_worker(NULL);
}
int main(void) {
 fresh();int value=40;supervisor_job good={.kind=SJ_CALL,.function=timely,.context=&value,.timeout_us=1000,.done=1};
 run(&good);assert(!resets&&notifications==1&&good.result==41&&value==41&&!supervisor_active);
 assert(!service_fault_pending());
 fresh();supervisor_job expired={.kind=SJ_CALL,.function=late,.timeout_us=1000,.done=1};
 /* No monitor ever ran: completion itself must catch the overdue return. */
 run(&expired);assert(resets==1&&notifications==0&&expired.result<0&&supervisor_fault);
 assert(service_fault_pending()&&lease_rtc.fault.reason==3&&lease_rtc.fault.seq==1);
 assert(!strcmp(lease_rtc.fault.role,"module-job")&&lease_rtc.fault.checkpoint==1);
 service_fault_status(0);assert(strstr(response,"\"reason\":3"));
 reset_reason=3;clock_us=0;service_watchdog_boot();assert(service_fault_pending());assert(service_fault_ack(1));
 fresh();init_result=-9;supervisor_job failed={.kind=SJ_INIT,.done=1};run(&failed);
 assert(resets==1&&notifications==0&&supervisor_fault&&!module_watchdog_configured());
 assert(lease_rtc.fault.reason==4&&!strcmp(lease_rtc.fault.role,"module-entry"));
 fresh();supervisor_job init={.kind=SJ_INIT,.done=1};run(&init);
 assert(!resets&&notifications==1&&module_watchdog_configured()&&!lease_initializing);
 fresh();supervisor_active=true;supervisor_started=supervisor_last_progress=1;
 supervisor_limit_us=1000;supervisor_stage=17;clock_us=1002;
 if(!setjmp(reset_trap))if(!setjmp(finished_trap))supervisor_monitor(NULL);
 assert(resets==1&&supervisor_fault&&service_fault_pending());
 assert(lease_rtc.fault.reason==3&&lease_rtc.fault.checkpoint==17);
 assert(!lock_depth);
 puts("PASS real worker completion deadline, no late success/notification, init failure and monitor persist sequence-aware RTC reports");
}
"""
with tempfile.TemporaryDirectory(
    prefix="rmb-supervisor-deadline-", dir=os.environ.get("TMPDIR", str(Path.home() / "tmp"))
) as work:
    path = Path(work)
    journal = (ROOT / "esp32_rom/main/service_watchdog.inc").read_text()
    (path / "deadline.c").write_text(
        preamble + types + journal + platform + worker + monitor + post
    )
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O1",
            "-g",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",
            "-Wno-unused-variable",
            "-fsanitize=address,undefined",
            "-fno-pie",
            "-no-pie",
            "-fno-sanitize-recover=all",
            "-I" + str(ROOT / "include"),
            "-I" + str(ROOT / "tests"),
                "-I" + str(ROOT / "esp32_rom" / "tests"),
            '-DRMB_PORT_PLATFORM_HEADER="port_native.h"',
            str(path / "deadline.c"),
            str(ROOT / "core/supervisor.c"),
            str(ROOT / "esp32_rom" / "tests" / "port_native_impl.c"),
            "-o",
            str(path / "deadline"),
        ],
        check=True,
    )
    subprocess.run([str(path / "deadline")], check=True, timeout=10)
