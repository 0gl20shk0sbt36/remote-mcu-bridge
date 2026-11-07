#!/usr/bin/env python3
"""Execute verbatim production request framing/auth/schema with official cJSON.
Only non-pad paths after status branch are omitted, not reimplemented.
"""
from pathlib import Path
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[1]
s = (ROOT/'module/business.c').read_text()
a = s.index('static void serve(')
b = s.index('    target_diag diagnostic', a)
c = s.index('    if(strcmp(op->valuestring,"spi-test")', b)
d = s.index('    if (!strcmp(op->valuestring, "status"))', c)
body = s[a:b] + s[c:d] + '\n failure(fd,"not pad op");}\n'
stubs = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include "cJSON.h"
#define REQUEST_MAX 1024
#define HEADER_US 1000000
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
static const char *input;
static unsigned offset, called, rejected, selected;
static bool supervisor_fault;
static int64_t esp_timer_get_time(void) {return 0;}
static bool socket_exact(int fd, void *p, unsigned n, bool wr, int64_t deadline) {
 (void)fd;(void)deadline;assert(!wr&&n==1); if(!input[offset])return false;
 *(char*)p=input[offset++];return true;
}
static bool authenticated(const char *s) {return !strcmp(s,"test-token");}
static void failure(int fd,const char *s) {(void)fd;(void)s;rejected++;}
static bool module_runtime_ready(void) {return true;}
static void spi_pad_sample(int fd,unsigned mode) {(void)fd;called++;selected=mode;}
'''
tests = r'''
static void check(const char *s,int expected) {
 input=s;offset=called=rejected=0;bool owned=false;serve(1,&owned);
 if(expected<0) assert(!called&&rejected);else assert(called==1&&!rejected&&selected==(unsigned)expected);
}
int main(void) {
 check("{\"token\":\"test-token\",\"op\":\"spi-pad-sample\"}\n",0);
 const char *pulls[]={"none","up","down"};char req[400];
 for(int i=0;i<3;i++) {snprintf(req,sizeof req,"{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"pull\":\"%s\"}\n",pulls[i]);check(req,i);}
 const char *bad[]={
 "{\"op\":\"spi-pad-sample\"}\n",
 "{\"token\":\"bad\",\"op\":\"spi-pad-sample\"}\n",
 "{\"token\":\"test-token\"}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"pull\":1}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"pull\":null}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"pull\":\"UP\"}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"gpio\":0}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"count\":16}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"size\":1}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"pull\":\"none\",\"pull\":\"up\"}\n",
 "{\"token\":\"test-token\",\"op\":\"spi-pad-sample\",\"pull\":\"none\\u0000up\"}\n",
 "[]\n"};
 for(unsigned i=0;i<sizeof bad/sizeof *bad;i++)check(bad[i],-1);
 puts("PASS production framing/auth/schema: optional none/up/down, missing auth/op, wrong token/types/pull, duplicate/unknown/extra/NUL keys rejected before sample");
}
'''
import importlib.util
deps_spec = importlib.util.spec_from_file_location('rmb_native_deps', ROOT.parent/'tools/native_deps.py')
if deps_spec is None or deps_spec.loader is None:
    raise RuntimeError('native dependency helper cannot be loaded')
deps = importlib.util.module_from_spec(deps_spec)
deps_spec.loader.exec_module(deps)
cjson = getattr(deps, 'cjson')()
with tempfile.TemporaryDirectory(prefix='spi-pad-request-') as work:
    p=Path(work);(p/'request.c').write_text(stubs+body+tests)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-variable','-Wno-unused-parameter','-fsanitize=address,undefined','-g','-I'+str(cjson),str(p/'request.c'),str(cjson/'cJSON.c'),'-lm','-o',str(p/'request')],check=True)
    subprocess.run([str(p/'request')],check=True)
