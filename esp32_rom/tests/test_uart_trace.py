#!/usr/bin/env python3
"""SDK-free executable tests of UART tracing extracted verbatim from business.c.

Only ESP-IDF hardware/console dependencies are mocked, not production callbacks.
Artifacts survive in TMPDIR (or ~/tmp) for inspection; no SDK configuration read.
"""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def rmb_tmp_dir():
    """Temporary base: TMPDIR, else ~/tmp when writable, else None (system temp).

    CI runners have no ~/tmp, so a hard-coded home directory is not portable.
    """
    explicit = os.environ.get("TMPDIR")
    if explicit:
        return explicit
    base = Path.home() / "tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return str(base)


ROOT = Path(__file__).resolve().parents[2]
STUBS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <limits.h>
#include "rom.h"
typedef int esp_err_t;
typedef unsigned TickType_t;
typedef void *QueueHandle_t;
typedef struct { int type; } uart_event_t;
enum { ESP_OK=0, pdTRUE=1, UART_FRAME_ERR=2, UART_PARITY_ERR,
       UART_FIFO_OVF, UART_BUFFER_FULL, UART_DATA };
#define UART_PORT 1
#define pdMS_TO_TICKS(x) ((TickType_t)(x))
#define CONFIG_ROM_SERVICE_RX 4
#define CONFIG_ROM_SERVICE_BOOT0 5
#define CONFIG_ROM_SERVICE_NRST 6
#define CONFIG_ROM_SERVICE_BOOT1 -1
static const char *TAG="host";
static char logs[262144];
static size_t log_used;
static unsigned writes, reads, waits, max_read, log_at_write;
static int write_result, wait_result, read_script[128], script_n, script_at;
static int event_script[256], event_n, event_at;
static size_t pending, consumed;
static bool noise, require_stopped;
static int get_error_at, get_calls;
static int64_t clock_us;
static bool *enabled_at_write;
static void host_log(const char *tag,const char *fmt,...) {
 (void)tag; va_list ap; va_start(ap,fmt);
 int n=vsnprintf(logs+log_used,sizeof logs-log_used,fmt,ap); va_end(ap);
 assert(n>=0 && (size_t)n+log_used+2<sizeof logs);
 log_used+=(size_t)n;logs[log_used++]='\n';logs[log_used]=0;
}
#define ESP_LOGI(...) host_log(__VA_ARGS__)
#define ESP_LOGW(...) host_log(__VA_ARGS__)
#define ESP_LOGE(...) host_log(__VA_ARGS__)
/* Supervisor heartbeat has no hardware dependency in this callback harness. */
static void supervisor_progress(void) {}
static int64_t esp_timer_get_time(void) { return clock_us+=100; }
static int gpio_get_level(int pin) { return pin&1; }
static int xQueueReceive(QueueHandle_t q,uart_event_t *e,int ticks) {
 (void)q;(void)ticks;
 if(event_at==event_n)return 0;
 e->type=event_script[event_at++];return pdTRUE;
}
static int uart_get_buffered_data_len(int port,size_t *n) {
 assert(port==UART_PORT);get_calls++;
 if(get_calls==get_error_at)return -1;
 *n=pending;return ESP_OK;
}
static int uart_read_bytes(int port,void *data,size_t n,TickType_t ticks) {
 (void)ticks;assert(port==UART_PORT);assert(++reads<10000);
 if(n>max_read)max_read=(unsigned)n;
 int got=script_at<script_n?read_script[script_at++]: (int)(n<pending?n:pending);
 if(got<=0)return got;
 assert((size_t)got<=n);
 for(int i=0;i<got;i++)((uint8_t *)data)[i]=(uint8_t)(consumed++);
 if(!noise) {assert(pending>=(size_t)got);pending-=(size_t)got;}
 return got;
}
static int uart_write_bytes(int port,const void *data,size_t n) {
 assert(port==UART_PORT);(void)data;writes++;log_at_write=(unsigned)log_used;
 if(require_stopped)assert(enabled_at_write && !*enabled_at_write);
 return write_result==INT_MAX?(int)n:write_result;
}
static int uart_wait_tx_done(int port,TickType_t ticks) {
 assert(port==UART_PORT && ticks==2000);waits++;return wait_result;
}
const char *rom_strerror(int rc) {(void)rc;return "mock I/O error";}
"""
TESTS = r"""
static target_diag fresh(void) {
 log_used=0;logs[0]=0;writes=reads=waits=max_read=log_at_write=0;
 pending=consumed=0;noise=require_stopped=false;enabled_at_write=NULL;
 write_result=INT_MAX;wait_result=0;script_n=script_at=0;
 event_n=event_at=0;get_error_at=get_calls=0;clock_us=0;
 return (target_diag){.stage="request",.phase="start",.ack_byte=-1,.first_byte_us=-1};
}
/* Independently decode console output, proving byte-for-byte completeness and
 * per-record bounds rather than trusting counters or one particular hex token. */
static unsigned check_hex(const char *scope,unsigned expected,unsigned first) {
 unsigned total=0,records=0;const char *p=logs;
 while((p=strstr(p," hex=["))) {
  const char *line=p;while(line>logs && line[-1]!='\n')line--;
  assert(strstr(line,scope) && strstr(line,scope)<p);
  const char *nptr=strstr(line," n=");unsigned declared=0;
  assert(nptr && nptr<p && sscanf(nptr," n=%u",&declared)==1);
  assert(declared>0 && declared<=32);
  p+=6;unsigned count=0;
  while(*p!=']') {
   unsigned byte;int used=0;
   assert(sscanf(p,"%2x %n",&byte,&used)==1 && used>0);
   assert(byte==((first+total)&255));p+=used;total++;count++;
  }
  assert(count==declared);records++;p++;
 }
 assert(total==expected);return records;
}
static void snapshots(void) {
 const unsigned sizes[]={17,31,32,33,255,2048,3072};
 for(unsigned i=0;i<sizeof sizes/sizeof sizes[0];i++) {
  target_diag d=fresh();pending=sizes[i];
  assert(target_raw_start(&d)==0);
  unsigned cap=sizes[i]<2048?sizes[i]:2048;
  assert(d.raw_rx==cap && d.rx_bytes==0 && consumed==cap);
  assert(pending==sizes[i]-cap && max_read<=32 && writes==0);
  assert(check_hex("operation-start",cap,0)==(cap+31)/32);
 }
 target_diag d=fresh();pending=5000;noise=true;
 assert(target_raw_start(&d)==0);assert(reads==64 && consumed==2048);
 assert(pending==5000 && d.raw_rx==2048 && writes==0);
 check_hex("operation-start",2048,0);
 d=fresh();assert(target_raw_start(&d)==0);log_used=0;logs[0]=0;
 pending=97;event_script[0]=UART_FRAME_ERR;event_script[1]=UART_PARITY_ERR;
 event_script[2]=UART_FIFO_OVF;event_script[3]=UART_BUFFER_FULL;event_n=4;
 assert(target_prepare(&d)==0 && d.prepared && d.stale_count==97);
 assert(d.frame_errors==0 && d.parity_errors==0 && d.fifo_overflows==0 && d.buffer_full==0);
 assert(strstr(logs,"frame=1 parity=1 fifo=1 full=1"));
 check_hex("pre-sync",97,0);assert(writes==0);
 puts("PASS full snapshots >16 bytes, <=32-byte chunks, 2048 cap, finite continuous noise, pre-sync counters");
}
static void snapshot_faults(void) {
 for(int err=-1;err<=0;err++) {
  target_diag d=fresh();pending=80;read_script[0]=7;read_script[1]=err;script_n=2;
  assert(target_raw_start(&d)==(err<0?-1:0));
  assert(consumed==7 && pending==73 && d.raw_rx==7 && writes==0);
  check_hex("operation-start",7,0);
 }
 for(int at=1;at<=2;at++) {
  target_diag d=fresh();pending=40;get_error_at=at;
  assert(target_raw_start(&d)==-1 && writes==0);
  assert(d.raw_rx==(at==1?0:40));
 }
 target_diag d=fresh();pending=20;get_error_at=1;uint8_t sync=0x7f;
 assert(target_tx(&d,&sync,1)==-1 && writes==0 && !d.prepared);
 puts("PASS partial/error snapshots and prepare failure never manufacture bytes or transmit");
}
static void transfers(void) {
 uint8_t data[80];for(unsigned i=0;i<sizeof data;i++)data[i]=(uint8_t)i;
 target_diag d=fresh();assert(target_raw_start(&d)==0);d.prepared=true;
 write_result=7;assert(target_tx(&d,data,sizeof data)==7);
 assert(d.tx_bytes==7 && d.raw_tx==7 && writes==1 && waits==1);
 check_hex("driver-accepted",7,0);
 d=fresh();target_raw_start(&d);d.prepared=true;write_result=5;wait_result=-1;
 assert(target_tx(&d,data,80)==-1 && d.tx_bytes==5 && d.raw_tx==5);
 check_hex("driver-accepted",5,0);
 assert(strstr(logs,"completion wait failed; physical delivery unknown"));
 const int failures[]={0,-1,81};
 for(unsigned k=0;k<3;k++) {
  d=fresh();target_raw_start(&d);d.prepared=true;write_result=failures[k];
  assert(target_tx(&d,data,80)<=0 && d.tx_bytes==0 && d.raw_tx==0 && waits==0);
 }
 for(int err=-1;err<=0;err++) {
  d=fresh();target_raw_start(&d);pending=80;
  read_script[0]=5;read_script[1]=3;read_script[2]=err;script_n=3;
  event_script[0]=UART_FRAME_ERR;event_script[1]=UART_PARITY_ERR;
  event_script[2]=UART_FIFO_OVF;event_script[3]=UART_BUFFER_FULL;event_n=4;
  assert(target_rx(&d,data,80,100)==8);
  assert(d.rx_bytes==8 && d.raw_rx==8 && pending==72 && d.first_byte_us>=0);
  assert(d.frame_errors==1 && d.parity_errors==1 && d.fifo_overflows==1 && d.buffer_full==1);
  check_hex("protocol",8,0);
  unsigned before=reads;assert(target_rx(&d,data,80,0)==0 && reads==before);
 }
 d=fresh();target_raw_start(&d);read_script[0]=-1;script_n=1;
 assert(target_rx(&d,data,80,100)==-1 && !d.rx_bytes && !d.raw_rx && d.first_byte_us==-1);
 puts("PASS short/failed TX, truthful completion failure, partial/error RX, actual-byte/error counters");
}
static void boundaries(void) {
 uint8_t command[]={0x31,0xce};
 for(int failure=0;failure<2;failure++) {
  target_diag d=fresh();target_raw_start(&d);d.prepared=true;
  target_trace(&d,"erase","command",1,0,0,-1);assert(d.raw_enabled);
  target_trace(&d,"write","command",2,0,0,-1);assert(!d.raw_enabled && writes==0);
  assert(strstr(logs,"raw trace STOP"));size_t stop=log_used;
  enabled_at_write=&d.raw_enabled;require_stopped=true;write_result=failure?-1:INT_MAX;
  assert(target_tx(&d,command,2)==(failure?-1:2));assert(log_at_write>=stop);
  target_trace(&d,"write","result",2,0,-5,-1);
  target_trace(&d,"read","command",3,0,0,-1);
  write_result=INT_MAX;target_tx(&d,command,2);pending=2;
  uint8_t rx[2];target_rx(&d,rx,2,100);
  assert(!d.raw_enabled && d.raw_tx==0 && d.raw_rx==0 && !strstr(logs+stop,"hex=["));
  target_raw_stop(&d,"terminal");assert(!strstr(logs+stop,"raw trace STOP"));
 }
 target_diag d=fresh();target_raw_start(&d);d.prepared=true;d.stage="write";
 enabled_at_write=&d.raw_enabled;require_stopped=true;target_tx(&d,command,2);
 assert(!d.raw_enabled && !d.raw_tx);
 d=fresh();d.probe=true;target_raw_start(&d);
 uint8_t bad=0x00;assert(target_tx(&d,&bad,1)==-1 && writes==0 && !d.prepared);
 uint8_t sync=0x7f;assert(target_tx(&d,&sync,1)==1 && d.prepared);
 const char *stages[]={"sync","get","get-version","get-id","read"};
 for(unsigned i=0;i<5;i++) {
  target_trace(&d,stages[i],"command",i,0,0,-1);
  /* A literal 0x31 outside write metadata must not stop raw tracing. */
  assert(target_tx(&d,command,2)==2);pending=2;uint8_t rx[2];
  assert(target_rx(&d,rx,2,100)==2 && d.raw_enabled);
 }
 target_trace(&d,"probe","result",5,0,-5,-1);assert(d.raw_enabled);
 assert(d.raw_tx==11 && d.raw_rx==10);
 unsigned n=writes;target_raw_stop(&d,"probe terminal (read-only)");
 target_raw(&d,true,"disabled",command,2,clock_us);
 assert(writes==n && !d.raw_enabled);
 d=fresh();target_raw_start(&d);target_trace(&d,"get","command",0,0,0,-1);
 target_raw(&d,true,"log-only",command,2,clock_us);target_raw_stop(&d,"terminal");
 assert(writes==0 && waits==0);
 puts("PASS stop precedes first write including failure; no restart; stage safety; probe remains traced; tracing alone never TX");
}
int main(void) {
 snapshots();snapshot_faults();transfers();boundaries();
 puts("PASS actual business.c UART trace host harness (SDK-free)");return 0;
}
"""


def main():
    source = (ROOT / "esp32_rom/module/business.c").read_text()
    end = source.index("/* Read the pad input path")
    prefix = source[: source.index("} target_diag;")]
    start = prefix.rfind("typedef struct {")
    if start < 0:
        raise RuntimeError("target_diag extraction marker missing")
    extracted = source[start:end]
    for name in (
        "target_events",
        "target_raw",
        "target_raw_stop",
        "target_snapshot_drain",
        "target_raw_start",
        "target_prepare",
        "target_trace",
        "target_tx",
        "target_rx",
    ):
        if not re.search(r"static\s+\w+\s+" + name + r"\s*\(", extracted):
            raise RuntimeError(f"production function missing: {name}")
    base = Path(rmb_tmp_dir() or tempfile.gettempdir()).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="uart-trace-host-", dir=base))
    harness = work / "uart_trace_host.c"
    harness.write_text(
        STUBS + '\n#line 1 "business.c"\n' + extracted + '\n#line 1 "host_tests.c"\n' + TESTS
    )
    binary = work / "uart_trace_host"
    command = shlex.split(os.environ.get("CC", "cc")) + [
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-pedantic",
        "-O1",
        "-g",
        "-I",
        str(ROOT / "rom"),
        str(harness),
        "-o",
        str(binary),
    ]
    print(f"ARTIFACTS: {work}", flush=True)
    build = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30
    )
    (work / "compile.log").write_text(build.stdout)
    if build.returncode:
        print(build.stdout)
        raise SystemExit(build.returncode)
    run = subprocess.run(
        [str(binary)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10
    )
    (work / "test.log").write_text(run.stdout)
    print(run.stdout, end="")
    raise SystemExit(run.returncode)


if __name__ == "__main__":
    main()
