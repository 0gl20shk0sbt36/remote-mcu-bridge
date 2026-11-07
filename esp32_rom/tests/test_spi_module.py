#!/usr/bin/env python3
"""ASan/UBSan tests of verbatim production business.c SPI pure helpers.

No ESP-IDF mocks or copied implementations; not a physical bus simulation.
Run from any directory: python3 esp32_rom/tests/test_spi_module.py
Native outputs use TMPDIR, never firmware build trees.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
pure = '#include "spi_diagnostic.h"\n'
harness = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
''' + pure + r'''
int main(void) {
  uint8_t tx[64], rx[64];
  const uint32_t seqs[]={0,1,2,10,63,100,255,256,65535,0xffffffffU};
  const char *details[]={"BUSY", "CS_LOW", "UART_FULL",
    "TIMEOUT rx=64 tx=64", "COUNT rx=63 tx=64", "EXTRA rx=64 tx=64",
    "OVR_OR_MODF rx=0 tx=1", "DMA rx=0 tx=1", "CS_GLITCH rx=64 tx=64",
    "DATA i=7 got=0 expected=49"};
  for(unsigned n=0;n<sizeof seqs/sizeof *seqs;n++) {
    uint32_t s=seqs[n]; spi_patterns(s,tx,rx);
    for(unsigned i=0;i<64;i++) {
      assert(tx[i]==((s+i)&255)); assert(rx[i]==((s^0xa5U^i)&255));
    }
    char line[128];
    snprintf(line,sizeof line,"SPI READY %lu",(unsigned long)s);
    assert(spi_parse_line(line,s,false)==1); assert(spi_parse_line(line,s,true)==0);
    snprintf(line,sizeof line,"SPI DONE %lu OK",(unsigned long)s);
    assert(spi_parse_line(line,s,true)==1); assert(spi_parse_line(line,s,false)==0);
    snprintf(line,sizeof line,"SPI DONE %lu ERR",(unsigned long)s);
    /* ERR aborts both READY and DONE waits; only success depends on phase. */
    for(unsigned done=0;done<2;done++) assert(spi_parse_line(line,s,done)==-1);
    for(unsigned k=0;k<sizeof details/sizeof *details;k++) {
      snprintf(line,sizeof line,"SPI DONE %lu ERR %s",(unsigned long)s,details[k]);
      for(unsigned done=0;done<2;done++) assert(spi_parse_line(line,s,done)==-1);
      /* Reject a matching grammar for a different sequence, including wrap. */
      for(unsigned done=0;done<2;done++) assert(!spi_parse_line(line,s+1U,done));
    }
  }
  const char *bad[]={"SPI READY 2","SPI READY 01","SPI READY 1 extra",
    " SPI READY 1","SPI READY 1x","heartbeat 1","SPI DONE 1 OK extra",
    "SPI DONE 1 OK\r","SPI DONE 2 OK","SPI DONE 2 ERR DATA i=7",
    "SPI DONE 10 ERR TIMEOUT rx=64 tx=64", "SPI DONE 01 ERR BUSY",
    "SPI DONE 1 ERROR", "SPI DONE 1 ERRsuffix", "SPI DONE 1 ERR\tDATA",
    "SPI DONE 1 ERR: DATA", " SPI DONE 1 ERR BUSY", "SPI DONE 1 ER", ""};
  for(unsigned i=0;i<sizeof bad/sizeof *bad;i++) {
    assert(!spi_parse_line(bad[i],1,false)); assert(!spi_parse_line(bad[i],1,true));
  }
  assert(spi_parameters_valid(1,100000)); assert(spi_parameters_valid(100,1000000));
  assert(spi_parameters_valid(10,100001));
  assert(!spi_parameters_valid(0,100000)); assert(!spi_parameters_valid(101,100000));
  assert(!spi_parameters_valid(1,99999)); assert(!spi_parameters_valid(1,1000001));
  assert(!spi_parameters_valid(0xffffffffU,100000));
  printf("PASS: actual C SPI helpers; %zu sequences x 64 bidirectional bytes; "
    "%zu detailed ERR reasons in READY/DONE phases; wrong sequence rejected; "
    "%zu malformed/unrelated lines incl ERR suffixes; 8 parameter boundaries\n",
    sizeof seqs/sizeof *seqs, sizeof details/sizeof *details, sizeof bad/sizeof *bad);
}
'''
with tempfile.TemporaryDirectory(prefix='spi-module-native-') as work:
    cfile = Path(work) / 'spi_module_native.c'
    exe = Path(work) / 'spi_module_native'
    cfile.write_text(harness)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', '-I'+str(ROOT / 'module/logic'),
                    str(ROOT / 'module/logic/spi_diagnostic.c'), str(cfile), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
