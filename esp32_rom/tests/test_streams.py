"""Compile actual stream implementation/auth/ring with SDK-only shims; no hardware claims."""

from pathlib import Path
import os
import re
import subprocess
import tempfile


def at(text, literal):
    """Locate a code anchor, tolerating formatter whitespace around tokens."""
    pattern = re.escape(literal)
    for token, flexible in (
        (r"\ ", r"\s*"),
        (",", r"\s*,\s*"),
        (r"\(", r"\(\s*"),
        (r"\)", r"\s*\)"),
        (r"\{", r"\{\s*"),
        (r"\}", r"\s*\}"),
        (";", r"\s*;"),
        ("=", r"\s*=\s*"),
    ):
        pattern = pattern.replace(token, flexible)
    match = re.search(pattern, text)
    if not match:
        raise ValueError("missing anchor: " + literal)
    return match.start()


def flat(text):
    """Compare C token runs without depending on formatting whitespace."""
    return re.sub(r"\s+", "", text)


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT.parent / "tools"))
from native_deps import cjson

CJSON = cjson()
service = (ROOT / "main/resident.c").read_text()
business = (ROOT / "module/business.c").read_text()
for include in ("uart_priority.inc", "uart_framed.inc"):
    business = business.replace(
        '#include "' + include + '"', (ROOT / "module" / include).read_text()
    )
# Device status is tested at the service boundary, not part of UART transport.
a = at(business, "static void service_status(")
b = business.index("static bool stream_auth_ex(", a)
business = business[:a] + business[b:]
backend = (ROOT / "module/platform/esp_idf_io.c").read_text()
uart_mode = backend[
    at(backend, "esp_err_t esp_io_uart_mode(") : at(backend, "static uint64_t pin_mask(")
]
# The resident owns logs; UART ownership/auth/framing now execute in business.
# Rename only resident-local symbols so both real implementations can coexist.
# Stop UART extraction before the SPI add-on: its SDK types are not UART shims.
resident_streams = (ROOT / "main/streams.inc").read_text().split("static void stream_server(")[0]
streams = (
    """\n#define stream_auth_ex log_stream_auth_ex
#define stream_auth log_stream_auth
#define stream_send log_stream_send
"""
    + resident_streams
    + """
#undef stream_auth_ex
#undef stream_auth
#undef stream_send
"""
    + business[
        at(business, "static esp_err_t target_uart_mode(") : business.rfind(
            "#if RMB_BUILTIN_SPI", 0, at(business, "static uint32_t spi_test_seq=1;")
        )
    ]
)
helpers = service[at(service, "bool authenticated(") : at(service, '#include "streams.inc"')]
helpers = helpers.replace('#include "module_runtime.h"', "")
preamble = r"""
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <unistd.h>
#include <time.h>
#include "cJSON.h"
#include "rmb/service.h"
#include "port_native.h"
static int native_read(rmb_connection c,void *b,size_t n,bool nb) {int r=(int)recv(c,b,n,nb?MSG_DONTWAIT:0);return r<0&&(errno==EAGAIN||errno==EWOULDBLOCK)?RMB_IO_AGAIN:r;}
static int native_write(rmb_connection c,const void *b,size_t n,bool nb) {int r=(int)send(c,b,n,nb?MSG_DONTWAIT:0);return r<0&&(errno==EAGAIN||errno==EWOULDBLOCK)?RMB_IO_AGAIN:r;}
static const rmb_net_api native_net={.read=native_read,.write=native_write};
static const rmb_host native_host={.net=&native_net,.now_us=esp_timer_get_time};
static const rmb_host *host=&native_host;
#define CONFIG_ROM_SERVICE_TOKEN "public-native-test-token"
#define CONFIG_RMB_TOKEN CONFIG_ROM_SERVICE_TOKEN
static void service_progress(void) {}
static void service_io_progress(void) {}
#define CONFIG_ROM_SERVICE_RX 4
#define BOARD_UART_RX CONFIG_ROM_SERVICE_RX
#define HEADER_US 100000
#define REQUEST_MAX 1024
#define UART_PORT 1
#define UART_PARITY_EVEN 2
#define UART_PARITY_DISABLE 0
#define GPIO_PULLUP_ONLY 1
#define ESP_OK 0
#define portMUX_TYPE int
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) do {(void)(x);assert(!pthread_mutex_lock(&state_mutex));} while (0)
#define portEXIT_CRITICAL(x) do {(void)(x);assert(!pthread_mutex_unlock(&state_mutex));} while (0)
typedef int esp_err_t;
typedef int (*vprintf_like_t)(const char *,va_list);
static int owner, uart_owner, mode_changes, last_parity, gives, console_calls;
static unsigned char uart_sent[256]; static atomic_size_t uart_sent_n; static atomic_bool uart_inject;
static int uart_set_baudrate(int p,unsigned b){(void)p;(void)b;mode_changes++;return 0;}
static int uart_set_parity(int p,int b){(void)p;last_parity=b;return 0;}
static int gpio_set_pull_mode(int p,int b){(void)p;(void)b;return 0;}
static pthread_mutex_t state_mutex=PTHREAD_MUTEX_INITIALIZER, owner_mutex=PTHREAD_MUTEX_INITIALIZER;
 (void)p;int64_t until=esp_timer_get_time()+(int64_t)t*1000;
 do {int rc=pthread_mutex_trylock(&owner_mutex);if(!rc){owner=1;return 1;}assert(rc==EBUSY);
 if(!t)break;
 struct timespec d={0,1000000};nanosleep(&d,NULL);
 }while(esp_timer_get_time()<until);return 0;
}
static int uart_flush_input(int p){(void)p;assert(owner);uart_inject=false;return 0;}
static void target_events(void *p){(void)p;assert(owner);}
static int uart_write_bytes(int p,const void *d,size_t n){(void)p;assert(owner);assert(n<=sizeof uart_sent);memcpy(uart_sent,d,n);uart_sent_n=n;return (int)n;}
static int uart_wait_tx_done(int p,int t){(void)p;(void)t;return 0;}
static int uart_read_bytes(int p,void *d,size_t n,int t){(void)p;(void)t;assert(owner);if(atomic_exchange(&uart_inject,false)){assert(n>=5);memcpy(d,"pong\0",5);uart_inject=false;return 5;}return 0;}
"""
tests = r"""
static void check_auth(const char *text,bool uart,bool expected,unsigned expected_baud) {
 int f[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,f)==0);
 assert(write(f[0],text,strlen(text))==(ssize_t)strlen(text));
 unsigned baud=0;bool history=false;
 assert((uart ? stream_auth(f[1],true,&baud,&history) : log_stream_auth(f[1],&history))==expected);
 if(expected && uart)assert(baud==expected_baud);
 close(f[0]);close(f[1]);
}
static int console(const char *f,va_list v){(void)f;(void)v;console_calls++;return 7;}
static int emit(const char *f,...){va_list v;va_start(v,f);int n=log_tee(f,v);va_end(v);return n;}

static void *worker(void *p){uart_framed_session(*(int*)p,115200);return NULL;}
static void data_send(int fd,uint32_t epoch,const char *text,size_t n){uint8_t b[128]={1,0,(uint8_t)(n+4)};relay_encode_epoch(b+3,epoch);memcpy(b+7,text,n);assert(write(fd,b,n+7)==(ssize_t)(n+7));}
static void read_record(int fd,int type,const char *event,uint32_t expected){uint8_t b[1031];assert(socket_exact(fd,b,3,false,esp_timer_get_time()+2000000));size_t n=((size_t)b[1]<<8)|b[2];assert(b[0]==type && n<=1028);assert(socket_exact(fd,b,n,false,esp_timer_get_time()+2000000));if(type==2){b[n]=0;cJSON*r=cJSON_Parse((char*)b);assert(r);assert(!strcmp(cJSON_GetObjectItem(r,"event")->valuestring,event));assert((uint32_t)cJSON_GetObjectItem(r,"epoch")->valuedouble==expected);cJSON_Delete(r);}}
static void wait_uart_sent(size_t expected){
 int64_t deadline=esp_timer_get_time()+2000000;
 while(atomic_load(&uart_sent_n)!=expected && esp_timer_get_time()<deadline)vTaskDelay(1);
 assert(atomic_load(&uart_sent_n)==expected);
}
static void check_priority_framed(void){
/* Authenticate the actual framed entry, then use the same implementation below. */
int auth[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,auth));
const char request[]="{\"token\":\"public-native-test-token\",\"op\":\"uart-framed\",\"baud\":115200}\n";
assert(write(auth[0],request,sizeof request-1)==(ssize_t)(sizeof request-1));
unsigned baud;bool history,framed=false;
assert(stream_auth_ex(auth[1],true,&baud,&history,&framed) && framed && baud==115200);
close(auth[0]);close(auth[1]);
int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));pthread_t thread;assert(!pthread_create(&thread,NULL,worker,&f[1]));
char ack[128];size_t n=0;do{assert(n<sizeof ack-1);assert(socket_exact(f[0],ack+n,1,false,esp_timer_get_time()+2000000));}while(ack[n++]!='\n');ack[n]=0;assert(strstr(ack,"\"framed\":1"));
data_send(f[0],1,"hi\0",3);wait_uart_sent(3);assert(uart_sent_n==3&&!memcmp(uart_sent,"hi\0",3));
int64_t began=esp_timer_get_time();assert(control_acquire());assert(esp_timer_get_time()-began<1500000);bool paused;uint32_t epoch;relay_snapshot(&paused,&epoch);assert(paused && epoch==2);read_record(f[0],2,"paused",2);
assert(!control_acquire());uart_inject=true;vTaskDelay(30);assert(uart_inject);size_t before=uart_sent_n;data_send(f[0],2,"bad",3);read_record(f[0],2,"write-rejected",2);assert(uart_sent_n==before);
relay_target="rom";control_release();assert(!uart_inject);read_record(f[0],2,"resumed",3);data_send(f[0],1,"stale",5);read_record(f[0],2,"write-rejected",3);assert(uart_sent_n==before);
data_send(f[0],3,"resumed\0",8);wait_uart_sent(8);assert(uart_sent_n==8&&!memcmp(uart_sent,"resumed\0",8));
shutdown(f[0],SHUT_RDWR);pthread_join(thread,NULL);assert(!owner);close(f[0]);close(f[1]);
/* A permanently non-reading peer still cannot delay ownership handoff. */
assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));int small=256;setsockopt(f[1],SOL_SOCKET,SO_SNDBUF,&small,sizeof small);uint8_t congestion[4096]={0};while(send(f[1],congestion,sizeof congestion,MSG_DONTWAIT)>0){};assert(errno==EAGAIN||errno==EWOULDBLOCK);assert(!pthread_create(&thread,NULL,worker,&f[1]));vTaskDelay(30);
for(int i=0;i<6;i++){began=esp_timer_get_time();assert(control_acquire());assert(esp_timer_get_time()-began<1500000);control_release();vTaskDelay(5);}
shutdown(f[0],SHUT_RDWR);pthread_join(thread,NULL);close(f[0]);close(f[1]);assert(!owner);
printf("PASS actual framed C: binary NUL, same connection pause/resume, paused/stale rejection, control reservation exclusion, six slow-client handoffs <1500ms; epoch=%u\n",relay_epoch);
}
int main(void){
 signal(SIGPIPE,SIG_IGN);
 check_auth("{\"token\":\"public-native-test-token\",\"op\":\"logs\"}\n",false,true,115200);
 check_auth("{\"token\":\"public-native-test-token\",\"op\":\"logs\",\"history\":false}\n",false,true,115200);
 check_auth("{\"token\":\"wrong\",\"op\":\"logs\"}\n",false,false,0);
 check_auth("{\"token\":\"public-native-test-token\",\"op\":\"uart\",\"baud\":57600}\n",true,true,57600);
 const char *bad[]={"{\"token\":\"public-native-test-token\",\"op\":\"uart\",\"baud\":1200}\n","{\"token\":\"public-native-test-token\",\"op\":\"uart\",\"baud\":115200.5}\n","{\"token\":\"public-native-test-token\",\"op\":\"uart\",\"baud\":-1}\n","{\"token\":\"public-native-test-token\",\"op\":\"uart\",\"baud\":4294967296}\n","{\"token\":\"public-native-test-token\",\"op\":\"logs\"}\n","{\"token\":\"public-native-test-token\",\"op\":\"uart\",\"op\":\"uart\"}\n","[1,2]\n","null\n","{broken}\n","{\"token\":\"public-native-test-token\\u0000evil\",\"op\":\"uart\"}\n"};
 for(size_t i=0;i<sizeof(bad)/sizeof(*bad);i++)check_auth(bad[i],true,false,0);
 unsigned allowed[]={9600,19200,38400,57600,115200};for(size_t i=0;i<5;i++)assert(stream_baud_allowed(allowed[i]));
 struct stream_ring r={0};char b[STREAM_RECORD_BYTES];memset(b,'a',sizeof b);uint64_t cursor=0,lost;
 for(int i=0;i<10000;i++)stream_push(&r,b,sizeof b);
 assert(stream_pop(&r,&cursor,b,&lost)==sizeof b && lost==9968);
 for(int i=0;i<31;i++)assert(stream_pop(&r,&cursor,b,&lost)==sizeof b && !lost);
 assert(!stream_pop(&r,&cursor,b,&lost));assert(sizeof(r)<=16384);
 console_print=console;assert(emit("%0600d",1)==7 && console_calls==1);
 cursor=0;size_t n=stream_pop(&log_ring,&cursor,b,&lost);assert(n==255);assert(!memcmp(b+n-17," [log truncated]\n",17));
 int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));assert(xSemaphoreTake(uart_owner,0));mode_changes=0;uart_session(f[1],115200);assert(owner==1 && !mode_changes && !gives);xSemaphoreGive(uart_owner);gives=0;close(f[0]);close(f[1]);
 assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));owner=0;shutdown(f[0],SHUT_WR);uart_session(f[1],115200);assert(!owner && gives==1 && last_parity==UART_PARITY_DISABLE);close(f[0]);close(f[1]);
 /* Binary full-duplex relay: no JSON parsing or payload logging after ack. */
 assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));assert(write(f[0],"ping\0",5)==5);shutdown(f[0],SHUT_WR);uart_inject=true;
 uart_session(f[1],57600);assert(!owner && gives==2 && uart_sent_n==5 && !memcmp(uart_sent,"ping\0",5));
 char wire[128];int count=(int)recv(f[0],wire,sizeof wire,0);assert(count==17 && !memcmp(wire,"{\"ok\":true}\npong\0",17));close(f[0]);close(f[1]);
 /* Authentication with no incoming bytes terminates at the finite deadline. */
 assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));unsigned baud;bool history;int64_t began=esp_timer_get_time();
 assert(!stream_auth(f[1],false,&baud,&history));assert(esp_timer_get_time()-began<500000);close(f[0]);close(f[1]);
 /* A non-reading peer cannot hold stream_send indefinitely. */
 assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));struct timeval tv={.tv_sec=0,.tv_usec=100000};assert(!setsockopt(f[1],SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof tv));
 static char huge[1024*1024];began=esp_timer_get_time();assert(!stream_send(f[1],huge,sizeof huge));assert(esp_timer_get_time()-began<1500000);close(f[0]);close(f[1]);
 /* Empty history and disconnected log subscriber terminate without UART access. */
 assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));shutdown(f[0],SHUT_WR);log_session(f[1],false);close(f[0]);close(f[1]);
 check_priority_framed();
 puts("PASS actual C stream auth, malformed/duplicate/range rejection, ring overwrite/truncation, console tee, UART busy/no-side-effects/release and log disconnect");
 return 0;
}
"""
with tempfile.TemporaryDirectory(
    prefix="v14-streams-", dir=os.environ.get("TMPDIR", str(Path.home() / "tmp"))
) as tmp:
    p = Path(tmp)
    (p / "test.c").write_text(preamble + uart_mode + helpers + streams + tests)
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-pthread",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I" + str(ROOT / "main"),
            "-I" + str(ROOT / "tests"),
            '-DRMB_PORT_PLATFORM_HEADER="port_native.h"',
            "-I" + str(ROOT.parent / "include"),
            "-I" + str(CJSON),
            str(p / "test.c"),
            str(CJSON / "cJSON.c"),
            "-lm",
            str(ROOT / "tests" / "port_native_impl.c"),
            "-o",
            str(p / "test"),
        ],
        check=True,
    )
    subprocess.run([str(p / "test")], check=True, timeout=10)
# Ownership before staging upload and target side effects; status remains independent.
assert (
    business.index("control_acquire()", at(business, "/* Only ESP staging"))
    < at(business, "esp_partition_erase_range")
    < at(business, '"Staging upload start')
)
status = business[
    at(business, 'if (!strcmp(op->valuestring, "status"))') : at(
        business, 'if (!strcmp(op->valuestring, "restart"))'
    )
]
assert "xSemaphore" not in status and "target_" not in status
assert flat("target_uart_mode(115200,true)") in flat(business) and flat(
    "target_uart_mode(115200,false)"
) in flat(business)
print("PASS structural control/staging ownership and ROM/APP mode restoration")
