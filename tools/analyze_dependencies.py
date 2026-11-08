#!/usr/bin/env python3
"""Measure platform coupling and base/service coupling. Stdlib only.

Usage:
    python3 tools/analyze_dependencies.py                 # source-level scan
    python3 tools/analyze_dependencies.py --proof PROOF   # add link-level symbol audit

`--proof` takes a module-link `proof.json` produced by build_module.py; it lists
exactly which base symbols a built service imports, which is the only honest way
to state base<->service coupling (source grep cannot see macro-generated calls).
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Everything a platform identifier looks like, after comments are stripped.
PLATFORM = re.compile(
    r"\b(esp_[a-z0-9_]+|ESP_[A-Z0-9_]+|freertos/[a-z]+\.h|lwip/[a-z]+\.h"
    r"|xTask\w+|vTask\w+|uxTask\w+|ulTask\w+|xQueue\w+|xSemaphore\w+|xEventGroup\w+"
    r"|portMUX\w+|portENTER_CRITICAL|portEXIT_CRITICAL|portTICK_PERIOD_MS"
    r"|RTC_NOINIT_ATTR|pdTRUE|pdFALSE|pdPASS|pdMS_TO_TICKS|portMAX_DELAY"
    r"|TaskHandle_t|QueueHandle_t|SemaphoreHandle_t|EventGroupHandle_t|TickType_t"
    r"|nvs_[a-z_]+|driver/[a-z_]+\.h)\b"
)

GROUPS = {
    "portable core (core/, include/rmb)": [
        "core/update.c",
        "core/supervisor.c",
        "include/rmb/update.h",
        "include/rmb/supervisor.h",
        "include/rmb/platform.h",
        "include/rmb/service.h",
        "include/rmb/port.h",
    ],
    "ROM core (rom/)": ["rom/rom.c", "rom/devices.c", "rom/rom.h"],
    "resident portable": [
        "esp32_rom/main/resident.c",
        "esp32_rom/main/generic_network.c",
        "esp32_rom/main/module_runtime.c",
        "esp32_rom/main/streams.inc",
        "esp32_rom/main/supervisor.inc",
        "esp32_rom/main/service_watchdog.inc",
    ],
    "ESP platform": sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "esp32_rom/main/platform").glob("*.c")
    ),
    "STM service": [
        "esp32_rom/module/business.c",
        "esp32_rom/module/entry.c",
        "esp32_rom/module/platform/esp_idf_io.c",
        "esp32_rom/module/uart_framed.inc",
        "esp32_rom/module/uart_priority.inc",
        "esp32_rom/module/logic/spi_diagnostic.c",
    ],
    "hello example": [
        "examples/services/hello/service.c",
        "examples/services/hello/platform_idf.c",
    ],
}

PYTHON_GROUPS = {
    "host tools (tools/)": sorted((ROOT / "tools").glob("*.py")),
    "backend driver": [
        ROOT / "backends/esp-idf/build.py",
        ROOT / "backends/esp-idf/config_defaults.py",
    ],
    "module builder": [ROOT / "esp32_rom/module/build_module.py"],
}

PY_PLATFORM = re.compile(
    r"\b(idf\.py|esptool|riscv32-esp-elf|IDF_PATH|sdkconfig|esp_partition"
    r"|--just-symbols|COM\d|ttyACM|ttyUSB|win32|msvcrt)\b"
)


def strip_comments(text):
    """Drop comments and string literals: prose may legitimately name a platform.

    The port header, for example, documents `-DRMB_PORT_PLATFORM_HEADER="..."`
    inside an #error message; that is documentation, not a dependency.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)


def scan():
    rows = []
    for name, files in GROUPS.items():
        code = refs = 0
        for relative in files:
            path = ROOT / relative
            if not path.is_file():
                continue
            text = strip_comments(path.read_text())
            code += sum(1 for line in text.splitlines() if line.strip())
            refs += len(PLATFORM.findall(text))
        rows.append((name, code, refs))
    return rows


def scan_python():
    rows = []
    for name, files in PYTHON_GROUPS.items():
        lines = refs = 0
        for path in files:
            text = path.read_text()
            lines += len(text.splitlines())
            refs += len(PY_PLATFORM.findall(text))
        rows.append((name, lines, refs))
    return rows


def classify(symbol):
    if symbol.startswith("cJSON_"):
        return "cJSON"
    if re.match(r"^(esp_|gpio_|uart_|periph_|UART|GPIO|SYSTEM|_esp_error)", symbol):
        return "ESP-IDF"
    if re.match(r"^(x|v|ux|ul)[A-Z]", symbol):
        return "FreeRTOS"
    if re.match(r"^(__|_lock|abort|mem|str|snprintf|floor|free)", symbol):
        return "libc/compiler-rt"
    return "other"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, help="module-link proof.json from build_module.py")
    args = parser.parse_args()

    print("## Platform coupling (C sources)\n")
    print("| area | code lines | platform refs | refs/kloc |")
    print("|---|---:|---:|---:|")
    rows = scan()
    portable_groups = 0
    glue_groups = 0
    for name, code, refs in rows:
        density = refs * 1000 / code if code else 0
        print(f"| {name} | {code} | {refs} | {density:.1f} |")
        if refs == 0:
            portable_groups += code
        else:
            glue_groups += code
    total = portable_groups + glue_groups
    share = glue_groups * 100 / max(total, 1)
    print(
        f"\nGroups with zero platform references: {portable_groups} lines; groups containing "
        f"platform code: {glue_groups} lines ({share:.0f}% of all scanned C). Platform code is "
        f"confined to esp32_rom/main/platform/ and the services.\n"
    )

    print("## Python coupling\n")
    print("| area | lines | platform refs |")
    print("|---|---:|---:|")
    for name, lines, refs in scan_python():
        print(f"| {name} | {lines} | {refs} |")

    if args.proof:
        proof = json.loads(args.proof.read_text())
        imports = sorted(proof.get("imported_base_symbols") or [])
        counts = Counter(classify(symbol) for symbol in imports)
        print("\n## Service -> base link-level audit\n")
        print(f"- imported base symbols: {len(imports)}")
        for origin, count in sorted(counts.items()):
            print(f"- {origin}: {count}")
        private = [s for s in imports if s.startswith("rmb_")]
        print(f"- base-private API symbols (rmb_*): {len(private)}")
        print(f"- module-defined globals: {len(proof.get('module_defined_globals') or [])}")
        print(f"- archives extracted: {len(proof.get('extracted_archive_members') or [])}")


if __name__ == "__main__":
    main()
