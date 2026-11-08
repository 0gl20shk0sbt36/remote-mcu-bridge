"""ABI4 SDK compilation and production architectural boundaries; no hardware claims."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServiceABI(unittest.TestCase):
    def test_sdk_standalone(self):
        header = (ROOT / 'include/rmb/service.h').read_text()
        includes = re.findall(r'^\s*#\s*include\s*[<"]([^>"]+)', header, re.M)
        self.assertTrue(set(includes) <= {'stdint.h', 'stddef.h', 'stdbool.h'}, includes)
        source = r'''
#include "rmb/service.h"
#include <assert.h>
_Static_assert(RMB_SERVICE_ABI == 4u, "ABI4");
_Static_assert(MODULE_ABI_VERSION == RMB_SERVICE_ABI, "image ABI");
_Static_assert(sizeof(rmb_connection) == sizeof(int32_t), "opaque handle width");
_Static_assert(RMB_NET_AUTH_CONSUME == 0u && RMB_NET_AUTH_REPLAY == 1u, "auth modes");
_Static_assert(RMB_IO_ERROR == -1 && RMB_IO_AGAIN == -2, "I/O errors");
#define AFTER(t,a,b) _Static_assert(offsetof(t,a)<offsetof(t,b), "field order")
AFTER(rmb_net_api,abi,size); AFTER(rmb_net_api,size,listen);
AFTER(rmb_net_api,listen,exact); AFTER(rmb_net_api,exact,read);
AFTER(rmb_net_api,read,write); AFTER(rmb_net_api,write,authenticated);
AFTER(rmb_host,abi,size); AFTER(rmb_host,size,net);
AFTER(rmb_host,net,now_us); AFTER(rmb_host,now_us,progress); AFTER(rmb_host,progress,run);
static void callback(rmb_connection c,void *p) {(void)c;(void)p;}
static int job(void *p) {(void)p;return 17;}
static int listen_fn(uint16_t p,uint32_t f,rmb_connection_fn cb,void *ctx) {(void)p;(void)f;cb(1,ctx);return 0;}
static bool exact_fn(rmb_connection c,void *b,size_t n,bool w,int64_t d) {(void)c;(void)b;(void)n;(void)w;(void)d;return true;}
static int read_fn(rmb_connection c,void *b,size_t n,bool nb) {(void)c;(void)b;(void)n;(void)nb;return 0;}
static int write_fn(rmb_connection c,const void *b,size_t n,bool nb) {(void)c;(void)b;(void)n;(void)nb;return 1;}
static bool auth(const char *s) {return s!=NULL;}
static int64_t now(void) {return 1;}
static void progress(void) {}
static int run(rmb_job_fn fn,void *ctx,int64_t limit) {assert(limit>0);return fn(ctx);}
static int watch_open(const char *role,uint32_t timeout) {assert(role && timeout);return 7;}
static int watch_feed(int lease,uint32_t checkpoint) {assert(lease==7 && checkpoint==1);return 0;}
static int watch_close(int lease) {assert(lease==7);return 0;}
static int entry(const rmb_host *h) {assert(h->net->listen(3333,0,callback,NULL)==0);return h->run(job,NULL,1);}
int main(void) {
 const rmb_net_api net={4,sizeof(rmb_net_api),listen_fn,exact_fn,read_fn,write_fn,auth};
 const rmb_host host={4,sizeof(rmb_host),&net,now,progress,run,watch_open,watch_feed,watch_close};
 module_entry_fn fn=entry;assert(fn(&host)==17);assert(host.watchdog_open("root",100)==7);assert(!host.watchdog_feed(7,1));assert(!host.watchdog_close(7));return 0;
}
'''
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', str(Path.home() / 'tmp'))) as d:
            p = Path(d)
            (p / 'sdk.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                            '-I'+str(ROOT/'include'), str(p/'sdk.c'), '-o', str(p/'sdk')], check=True)
            subprocess.run([str(p/'sdk')], check=True)

    def test_resident_has_no_target_dependencies(self):
        files = list((ROOT/'esp32_rom/main').glob('*.c')) + list((ROOT/'esp32_rom/main').glob('*.h')) + list((ROOT/'esp32_rom/main').glob('*.inc'))
        forbidden = re.compile(r'\b(?:rom_device|rom_io|rom_source|rom_probe_result|module_ops|module_rom_\w+|module_runtime_dispatch|SJ_VALIDATE|SJ_DOWNLOAD|SJ_PROBE|SJ_RESTART|CONFIG_ROM_SERVICE_DEVICE_\w+|BOOT0|NRST)\b|["<]rom\.h[">]|"staging"')
        for path in files:
            # Comments cannot create compiled dependencies.
            text = re.sub(r'/\*.*?\*/|//[^\n]*', '', path.read_text(), flags=re.S)
            self.assertIsNone(forbidden.search(text), str(path))
        cmake = (ROOT/'esp32_rom/main/CMakeLists.txt').read_text()
        self.assertNotIn('rom/devices.c', cmake)
        self.assertNotIn('rom/rom.c', cmake)

    def test_hello_has_no_rom_or_raw_socket_imports(self):
        files = list((ROOT/'examples/services/hello').glob('*.c'))
        self.assertTrue(files)
        for path in files:
            if 'test' in path.name:
                continue
            source = re.sub(r'/\*.*?\*/|//[^\n]*', '', path.read_text(), flags=re.S)
            self.assertNotRegex(source, r'\b(?:module_ops|rom_io|rom_device|socket|accept|recv|send|close)\s*\(')
            self.assertNotRegex(source, r'["<](?:rom\.h|sys/socket\.h|lwip/sockets\.h)[">]')
            self.assertNotIn('module_ops', source)


if __name__ == '__main__':
    unittest.main()
