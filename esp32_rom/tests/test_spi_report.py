#!/usr/bin/env python3
"""Compile the verbatim production SPI JSON formatter; fixtures, not hardware."""

from pathlib import Path
import json
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "module/business.c").read_text()
start = source.index("    char report[2200];", source.index("static void spi_test("))
end = re.search(r"reply\(fd,\s*report\);", source[start:]).start() + start
formatter = source[start:end]
harness = (
    r"""
#include <stdio.h>
#include <string.h>
#include "spi_diagnostic.h"
typedef struct {int level; unsigned iomux,matrix;} esp_io_spi_pad_snapshot;
static void esp_io_spi_pad_evidence(esp_io_spi_pad_snapshot *b, esp_io_spi_pad_snapshot *d) {*b=(esp_io_spi_pad_snapshot){1,512,64};*d=*b;}
static void emit(spi_diagnostic_result r,unsigned long count,unsigned long hz) {
"""
    + formatter
    + r"""
 puts(report);
}
int main(void) {
 spi_diagnostic_result r={.stage="spi-rx-verify",.error="ESP RX pattern mismatch",
  .last_seq=101,.rx_mismatch_bytes=63,.stm_rx_verified=1,.rx_failure_captured=true,
  .rx_failure_seq=101,.first_mismatch_index=0,.first_mismatch_actual=255,
  .first_mismatch_expected=192,.rx_min=255,.rx_max=255,.rx_constant=true,
  .rx_first16_hex="ffffffffffffffffffffffffffffffff",
  .expected_first16_hex="c0c1c2c3c4c5c6c7c8c9cacbcccdcecf"};
 emit(r,100,500000);
 memset(&r,0,sizeof r);r.ok=true;r.stage="complete";r.error="none";emit(r,1,100000);
}
"""
)
with tempfile.TemporaryDirectory(prefix="spi-report-native-") as work:
    c = Path(work) / "report.c"
    exe = Path(work) / "report"
    c.write_text(harness)
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsanitize=address,undefined",
            "-g",
            "-I" + str(ROOT / "module/logic"),
            str(c),
            "-o",
            str(exe),
        ],
        check=True,
    )
    reports = [
        json.loads(line)
        for line in subprocess.check_output([str(exe)], text=True).splitlines()
        if line
    ]
    bad, good = reports
    assert bad["rx_failure_captured"] and bad["rx_failure_seq"] == 101
    assert bad["rx_first16_hex"] == "ff" * 16
    assert bad["expected_first16_hex"] == "c0c1c2c3c4c5c6c7c8c9cacbcccdcecf"
    assert bad["first_mismatch_index"] == 0 and bad["first_mismatch_actual"] == 255
    assert bad["first_mismatch_expected"] == 192
    assert bad["rx_min"] == bad["rx_max"] == 255 and bad["rx_constant"]
    assert not bad["ok"] and bad["stm_rx_verified"] == 1
    assert good["ok"] and not good["rx_failure_captured"]
    assert good["rx_first16_hex"] == good["expected_first16_hex"] == ""
    for key in (
        "first_mismatch_index",
        "first_mismatch_actual",
        "first_mismatch_expected",
        "rx_min",
        "rx_max",
    ):
        assert good[key] == -1
    assert bad["rx_scope"] == good["rx_scope"] == "ESP driver RX bytes, not remote pad measurement"
print(
    "PASS production SPI JSON formatter: bounded hex, mismatch metadata, absent-evidence sentinels, valid complete JSON"
)
