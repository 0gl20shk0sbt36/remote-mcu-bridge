"""Compile production generic network C with native socket/RTOS shims."""

from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / "esp32_rom/main/generic_network.c").read_text()
# Strip includes only; all production functions and state are compiled verbatim.
source = "\n".join(line for line in source.splitlines() if not line.startswith("#include"))
pre = r"""
#define _POSIX_C_SOURCE 200809L
#include "rmb/service.h"
#include "port_native.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <time.h>
#include <sys/wait.h>
#ifndef CONFIG_RMB_LISTENER_STACK_BYTES
#define CONFIG_RMB_LISTENER_STACK_BYTES 12288
#endif
static int mutex;
static void vSemaphoreDelete(int *p) {(void)p;}
static bool code_allowed=true;
static void *current_task=(void *)1;
static unsigned task_creates, task_notifies, task_deletes;
static int task_fail_after=-1;
bool module_runtime_code_pointer(const void *p) {return p!=NULL && code_allowed;}
bool module_runtime_ready(void) {return true;}
bool authenticated(const char *s) {return s && !strcmp(s,"native-public-token");}
void rmb_network_init_abort(void);
"""
service = (ROOT / "esp32_rom/main/resident.c").read_text()
pre += service[service.index("bool socket_exact(") : service.index("bool reply(")]
post = r"""
static void check(const char *text,size_t n,bool expected) {
 int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));assert(write(f[0],text,n)==(ssize_t)n);shutdown(f[0],SHUT_WR);
 connection_slot slot={.fd=f[1],.flags=RMB_NET_AUTH_REPLAY};
 assert(auth_header(&slot)==expected);
 if(expected) {assert(slot.replay_size==n);assert(!memcmp(slot.replay,text,n));}
 close(f[0]);close(f[1]);
}
#define CHECK(s,e) check(s,sizeof(s)-1,e)
static void handler(rmb_connection h,void *p) {(void)h;(void)p;assert(!"must not invoke before commit/readiness");}
static uint16_t free_port(void) {
 int probe=socket(AF_INET,SOCK_STREAM,0);assert(probe>=0);
 struct sockaddr_in a={.sin_family=AF_INET,.sin_addr={.s_addr=htonl(INADDR_LOOPBACK)}};
 assert(!bind(probe,(struct sockaddr *)&a,sizeof a));socklen_t n=sizeof a;
 assert(!getsockname(probe,(struct sockaddr *)&a,&n));close(probe);return ntohs(a.sin_port);
}
static void rejected_transaction(unsigned kind) {
 pid_t pid=fork();assert(pid>=0);
 if(!pid) {
  rmb_network_init_begin();uint16_t p=free_port();assert(!net_listen(p,0,handler,NULL));
  int rc=0;
  switch(kind) {
   case 0:rc=net_listen(0,0,handler,NULL);break;
   case 1:rc=net_listen(3335,0,handler,NULL);break;
   case 2:rc=net_listen(3337,0,handler,NULL);break;
   case 3:rc=net_listen(free_port(),2,handler,NULL);break;
   case 4:rc=net_listen(free_port(),0,NULL,NULL);break;
   case 5:code_allowed=false;rc=net_listen(free_port(),0,handler,NULL);break;
   case 6:rc=net_listen(p,0,handler,NULL);break;
   case 7:for(unsigned i=1;i<LISTENERS;i++)assert(!net_listen(free_port(),0,handler,NULL));rc=net_listen(free_port(),0,handler,NULL);break;
   default:assert(!net_listen(free_port(),0,handler,NULL));task_fail_after=1;rc=-1;break;
  }
  assert(rc<0);assert(!rmb_network_init_commit());assert(atomic_load(&phase)==ABORTED);
  assert(!task_notifies);if(kind==8)assert(task_creates==1&&task_deletes==1);else assert(!task_creates);assert(slots[0].listener==-1&&!slots[0].lock);
  int fd=socket(AF_INET,SOCK_STREAM,0);assert(fd>=0);
  struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(p),.sin_addr={.s_addr=htonl(INADDR_LOOPBACK)}};
  assert(!bind(fd,(struct sockaddr *)&a,sizeof a));close(fd);
  rmb_network_init_begin();assert(atomic_load(&phase)==ABORTED);_exit(0);
 }
 int status;assert(waitpid(pid,&status,0)==pid);assert(WIFEXITED(status)&&!WEXITSTATUS(status));
}
int main(void) {
 CHECK("{\"token\":\"native-public-token\"}\n",true);
 CHECK("{\"op\":\"arbitrary\",\"nested\":[true,null,{\"token\":17}],\"token\":\"native-public-token\"}\n",true);
 CHECK("{\"t\\u006fken\":\"native-public-token\"}\n",true);
 CHECK("{\"token\":\"wrong\"}\n",false);
 CHECK("{\"token\":\"native-public-token\",\"token\":\"native-public-token\"}\n",false);
 CHECK("{\"token\":\"native-public-token\",\"t\\u006fken\":\"native-public-token\"}\n",false);
 CHECK("{\"token\":\"native-public-token\\u0000suffix\"}\n",false);
 CHECK("{\"token\":\"native-public-token\",\"ignored\":\"\\u0000\"}\n",false);
 CHECK("{\"token\":\"native-public-token\",\"ignored\":\"\0\"}\n",false);
 CHECK("{\"token\":17}\n",false);CHECK("[]\n",false);CHECK("{}\n",false);
 CHECK("{\"token\":\"native-public-token\"}{}\n",false);
 CHECK("{\"token\":\"native-public-token\",\"n\":01}\n",false);
 CHECK("{\"token\":\"native-public-token\",\"n\":1e}\n",false);
 CHECK("{\"token\":\"native-public-token\",\"n\":\"\\ud800\"}\n",false);
 char big[2100];memset(big,' ',sizeof big);check(big,sizeof big,false);
 int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));assert(nonblock(f[1]));
 slots[0]=(connection_slot){.fd=f[1],.generation=7,.active=true,.lock=&mutex};
 rmb_connection h=7*LISTENERS+1;char b[256];
 assert(net_read(h,b,sizeof b,true)==RMB_IO_AGAIN);
 assert(net_write(h,"abc",3,false)==3);assert(read(f[0],b,3)==3&&!memcmp(b,"abc",3));
 assert(write(f[0],"q",1)==1);assert(net_read(h,b,sizeof b,false)==1&&b[0]=='q');
 assert(net_write(h,"",0,true)==0);
 assert(write(f[0],"xy",2)==2);assert(net_exact(h,b,2,false,esp_timer_get_time()+100000));assert(!memcmp(b,"xy",2));
 assert(!net_exact(h,b,1,false,esp_timer_get_time()-1));
 /* A partially completed exact read cannot reset its absolute deadline. */
 assert(write(f[0],"r",1)==1);int64_t partial=esp_timer_get_time();
 assert(!net_exact(h,b,2,false,partial+20000));assert(b[0]=='r'&&esp_timer_get_time()-partial<200000);
 int64_t began=esp_timer_get_time();assert(!net_exact(h,b,1,false,began+20000));assert(esp_timer_get_time()-began<200000);
 slots[0].active=false;assert(net_read(h,b,1,false)==RMB_IO_ERROR);
 slots[0].active=true;slots[0].generation=8;assert(net_write(h,"bad",3,false)==RMB_IO_ERROR);
 assert(!net_exact(h,b,1,false,esp_timer_get_time()+10000));
 h=8*LISTENERS+1;assert(net_read(h,b,0,false)==0);
 assert(net_read(h,NULL,1,false)==RMB_IO_ERROR);assert(net_read(0,b,1,false)==RMB_IO_ERROR);
 const char header[]="{\"token\":\"native-public-token\"}\n";
 assert(write(f[0],header,sizeof header-1)==sizeof header-1);
 slots[0].flags=RMB_NET_AUTH_REPLAY;assert(auth_header(&slots[0]));
 assert(net_exact(h,b,sizeof header-1,false,esp_timer_get_time()+100000));assert(!memcmp(b,header,sizeof header-1));
 assert(net_read(h,b,1,true)==RMB_IO_AGAIN); /* no authentication ACK */
 assert(write(f[0],header,sizeof header-1)==sizeof header-1);assert(write(f[0],"z",1)==1);
 slots[0].flags=RMB_NET_AUTH_CONSUME;assert(auth_header(&slots[0]));assert(net_read(h,b,1,false)==1&&b[0]=='z');
 shutdown(f[0],SHUT_WR);assert(net_read(h,b,1,false)==0);close(f[0]);close(f[1]);
 memset(slots,0,sizeof slots);
 assert(net_listen(3333,0,handler,NULL)<0);
 /* Each poison case has a genuinely fresh process/boot transaction. */
 for(unsigned i=0;i<9;i++)rejected_transaction(i);
 rmb_network_init_begin();
 current_task=(void *)9;assert(net_listen(3333,0,handler,NULL)<0);current_task=(void *)1;
 for(unsigned i=0;i<LISTENERS;i++) {
  int probe=socket(AF_INET,SOCK_STREAM,0);assert(probe>=0);
  struct sockaddr_in addr={.sin_family=AF_INET,.sin_addr={.s_addr=htonl(INADDR_LOOPBACK)}};
  assert(!bind(probe,(struct sockaddr *)&addr,sizeof addr));socklen_t len=sizeof addr;
  assert(!getsockname(probe,(struct sockaddr *)&addr,&len));uint16_t port=ntohs(addr.sin_port);close(probe);
  assert(!net_listen(port,0,handler,NULL));
 }
 assert(!task_creates&&!task_notifies);
 assert(rmb_network_init_commit());assert(task_creates==LISTENERS&&task_notifies==LISTENERS);
 assert(!rmb_network_init_commit());assert(net_listen(4444,0,handler,NULL)<0);
 rmb_network_init_abort();assert(task_deletes==LISTENERS);
 for(unsigned i=0;i<LISTENERS;i++)assert(slots[i].listener==-1&&!slots[i].active&&!slots[i].lock);
 rmb_network_init_begin();assert(atomic_load(&phase)==ABORTED);
 puts("PASS production ABI4 auth, decoded duplicate/NUL rejection, replay/consume, stale generations, binary I/O, EOF, nonblocking and exact deadlines");
}
"""
with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", str(Path.home() / "tmp"))) as d:
    p = Path(d)
    (p / "network.c").write_text(pre + source + post)
    for stack_bytes in (12288, 16384):
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-misleading-indentation",
                "-fsanitize=address,undefined",
                "-fno-pie",
                "-no-pie",
                "-fno-sanitize-recover=all",
                f"-DCONFIG_RMB_LISTENER_STACK_BYTES={stack_bytes}",
                "-I" + str(ROOT / "include"),
                "-I" + str(ROOT / "tests"),
                "-I" + str(ROOT / "esp32_rom" / "tests"),
                '-DRMB_PORT_PLATFORM_HEADER="port_native.h"',
                str(p / "network.c"),
                str(ROOT / "esp32_rom" / "tests" / "port_native_impl.c"),
                "-o",
                str(p / "network"),
            ],
            check=True,
        )
        subprocess.run([str(p / "network")], check=True, timeout=10)
