#!/usr/bin/env python3
"""Deadline regressions using actual base/network/STM/hello exact-I/O C.

Only clocks, operation feeds and platform scheduling are shimmed. Base and
network paths use real localhost socketpairs; this is not MCU timing proof.
"""

import ast
import os
from pathlib import Path
import re
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


ROOT = Path(__file__).resolve().parents[1]
fixture = ast.parse((ROOT / "esp32_rom/tests/test_generic_network.py").read_text())
pre = next(
    node.value.value
    for node in fixture.body
    if isinstance(node, ast.Assign)
    and isinstance(node.value, ast.Constant)
    and any(isinstance(t, ast.Name) and t.id == "pre" for t in node.targets)
)
assert isinstance(pre, str)
pre, count = re.subn(
    r"static int64_t esp_timer_get_time\(void\) \{[^\n]*\}",
    "static int64_t fake_time=1;\nstatic int64_t esp_timer_get_time(void) {return fake_time;}",
    pre,
)
assert count == 1
wrappers = r"""
static int64_t final_io_time=-1,copy_time=-1;
static ssize_t timed_recv(int fd,void *p,size_t n,int flags) {
 ssize_t result=recv(fd,p,n,flags);
 if(result>0&&final_io_time>=0)fake_time=final_io_time;
 return result;
}
static ssize_t timed_send(int fd,const void *p,size_t n,int flags) {
 ssize_t result=send(fd,p,n,flags);
 if(result>0&&final_io_time>=0)fake_time=final_io_time;
 return result;
}
static void *timed_copy(void *d,const void *s,size_t n) {
 void *result=memcpy(d,s,n);if(copy_time>=0)fake_time=copy_time;return result;
}
#define recv timed_recv
#define send timed_send
#define memcpy timed_copy
/* Primitive creators come from the shared prelude; only the queue-send hook
 * (generic_network creates one mutex per listener) is fixture-specific here. */
"""
base = (ROOT / "esp32_rom/main/resident.c").read_text()
base = base[base.index("bool socket_exact(") : base.index("bool reply(")]
network = (ROOT / "esp32_rom/main/generic_network.c").read_text()
network = "\n".join(line for line in network.splitlines() if not line.startswith("#include"))
module_shims = r"""
static const rmb_host *host,*sdk;
static void service_io_progress(void) {}
static bool active_progress(int lease,uint32_t checkpoint) {(void)lease;(void)checkpoint;return true;}
static void hello_platform_yield(void) {++fake_time;}
static void module_delay(unsigned n) {fake_time+=n;}
#define vTaskDelay module_delay
static int module_read(rmb_connection h,void *p,size_t n,bool nonblocking) {
 assert(h==77&&nonblocking&&n);*(char *)p='Q';
 if(final_io_time>=0)fake_time=final_io_time;
 return 1;
}
static int module_write(rmb_connection h,const void *p,size_t n,bool nonblocking) {
 assert(h==77&&nonblocking&&p&&n);
 if(final_io_time>=0)fake_time=final_io_time;
 return 1;
}
"""
stm = (ROOT / "esp32_rom/module/service_net.h").read_text()
stm = stm[stm.index("static bool socket_exact(") : stm.index("static bool authenticated(")]
stm = stm.replace("socket_exact(", "stm_exact(", 1)
hello = (ROOT / "examples/services/hello/service.c").read_text()
hello = hello[hello.index("static bool socket_exact(") : hello.index("static bool reply(")]
hello = hello.replace("socket_exact(", "hello_exact(", 1)
# After every fixture mock, before any production unit: the shim turns the
# port contract into calls on the mocks above, so the code under test keeps
# its instrumentation while talking only to rmb/port.h.
seam = '#include "port_compat.h"\n'
post = r"""
int main(void) {
 for(unsigned layer=0;layer<2;layer++)for(unsigned writing=0;writing<2;writing++)
  for(int64_t finished=999;finished<=1001;finished++) {
   int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));assert(nonblock(f[1]));
   slots[0]=(connection_slot){.fd=f[1],.generation=1,.active=true,.lock=&mutex};
   char byte='Q';if(!writing)assert(write(f[0],&byte,1)==1);
   fake_time=1;final_io_time=finished;copy_time=-1;
   bool accepted=layer?net_exact(LISTENERS+1,&byte,1,writing,1000):socket_exact(f[1],&byte,1,writing,1000);
   assert(accepted==(finished<1000));
   /* An expired write may already have side effects; never claim timely success. */
   if(writing)assert(read(f[0],&byte,1)==1&&byte=='Q');
   close(f[0]);close(f[1]);
  }
 for(int64_t finished=999;finished<=1001;finished++) {
  int f[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,f));
  slots[0]=(connection_slot){.fd=f[1],.generation=1,.active=true,.lock=&mutex,.replay_size=1};
  slots[0].replay[0]='Q';char byte;
  fake_time=1;final_io_time=-1;copy_time=finished;
  bool accepted=net_exact(LISTENERS+1,&byte,1,false,1000);
  assert(accepted==(finished<1000));copy_time=-1;
  close(f[0]);close(f[1]);
 }
 rmb_net_api net={.read=module_read,.write=module_write};rmb_host h={.net=&net,.now_us=esp_timer_get_time};host=sdk=&h;
 for(unsigned layer=0;layer<2;layer++)for(unsigned writing=0;writing<2;writing++)
  for(int64_t finished=999;finished<=1001;finished++) {
   fake_time=1;final_io_time=finished;char byte='Q';
   bool accepted=layer?hello_exact(77,&byte,1,writing,1000,1):stm_exact(77,&byte,1,writing,1000);
   assert(accepted==(finished<1000));
  }
 fake_time=1000;assert(!socket_exact(0,NULL,0,false,1000));
 assert(!stm_exact(77,NULL,0,false,1000));assert(!hello_exact(77,NULL,0,false,1000,1));
 puts("PASS actual base/network/STM/hello final-byte deadlines: before accepted, equality/late rejected for read/write/replay");
}
"""
with tempfile.TemporaryDirectory(prefix="rmb-exact-deadline-", dir=rmb_tmp_dir()) as work:
    path = Path(work)
    (path / "deadline.c").write_text(
        pre + wrappers + module_shims + seam + base + network + stm + hello + post
    )
    subprocess.run(
        [
            "cc",
            "-I"
            + str((ROOT / "include") if (ROOT / "include").is_dir() else (ROOT.parent / "include")),
            "-I"
            + str(
                next(
                    p
                    for p in (ROOT / "tests", ROOT / "esp32_rom" / "tests", ROOT.parent / "tests")
                    if (p / "port_native.h").is_file()
                )
            ),
            '-DRMB_PORT_PLATFORM_HEADER="port_mock_platform.h"',
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-misleading-indentation",
            # Port logging drops its arguments, so a few mocks the production
            # code used to call directly are now unreachable from this TU.
            "-Wno-unused-function",
            "-fsanitize=address,undefined",
            "-fno-pie",
            "-no-pie",
            "-fno-sanitize-recover=all",
            "-I" + str(ROOT / "include"),
            str(path / "deadline.c"),
            "-o",
            str(path / "deadline"),
        ],
        check=True,
    )
    subprocess.run([str(path / "deadline")], check=True, timeout=10)
