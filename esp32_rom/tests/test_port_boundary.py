"""Resident/port boundary: the portable half must not name platform APIs.

Ported from the refactor contract: a new host implements rmb/port.h and nothing
else, so any platform identifier leaking into the portable sources is a porting
cost that must be caught here rather than discovered during a port.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

PORTABLE = [
    ROOT / "main/resident.c",
    ROOT / "main/generic_network.c",
    ROOT / "main/generic_network.h",
    ROOT / "main/module_runtime.c",
    ROOT / "main/module_runtime.h",
    ROOT / "main/module_abi.h",
    ROOT / "main/stream_ring.h",
    ROOT / "main/streams.inc",
    ROOT / "main/supervisor.inc",
    ROOT / "main/service_watchdog.inc",
    REPO / "include/rmb/port.h",
    REPO / "include/rmb/service.h",
    REPO / "core/update.c",
    REPO / "core/supervisor.c",
]

# Any of these in a portable file means the port boundary leaked.
FORBIDDEN = re.compile(
    r"\b("
    r"esp_[a-z0-9_]+|ESP_[A-Z0-9_]+|nvs_[a-z0-9_]+|lwip_[a-z0-9_]+"
    r"|xTask[A-Za-z]*|vTask[A-Za-z]*|uxTask[A-Za-z]*|ulTask[A-Za-z]*"
    r"|xQueue[A-Za-z]*|xSemaphore[A-Za-z]*|xEventGroup[A-Za-z]*"
    r"|portMUX_[A-Z_]+|portENTER_CRITICAL|portEXIT_CRITICAL|portTICK_PERIOD_MS"
    r"|RTC_NOINIT_ATTR|pdTRUE|pdFALSE|pdPASS|pdMS_TO_TICKS|portMAX_DELAY"
    r"|TaskHandle_t|QueueHandle_t|SemaphoreHandle_t|EventGroupHandle_t|TickType_t"
    r")\b"
)


def strip_comments(text):
    """Drop C comments and string literals so prose cannot trip the scan."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)


def portable_violations():
    found = []
    for path in PORTABLE:
        code = strip_comments(path.read_text())
        for match in FORBIDDEN.finditer(code):
            line = code[: match.start()].count("\n") + 1
            found.append(f"{path.relative_to(REPO)}:{line}: {match.group(0)}")
    return found


def port_contract_functions():
    """Function names the contract requires every platform to implement."""
    header = (REPO / "include/rmb/port.h").read_text()
    return set(re.findall(r"\b(rmb_port_[a-z0-9_]+)\s*\(", header))


def platform_definitions():
    names = set()
    for path in sorted((ROOT / "main/platform").glob("*.c")):
        code = strip_comments(path.read_text())
        names |= set(re.findall(r"\b(rmb_port_[a-z0-9_]+)\s*\([^;{]*\)\s*\{", code))
    return names


def main():
    violations = portable_violations()
    assert not violations, "platform identifiers in portable resident code:\n" + "\n".join(
        sorted(violations)
    )

    contract = port_contract_functions()
    implemented = platform_definitions()
    assert contract, "port.h declares no rmb_port_* functions"
    missing = sorted(contract - implemented)
    assert not missing, "platform does not implement: " + ", ".join(missing)

    # The platform layer is the only place that may include platform headers.
    for path in sorted((ROOT / "main/platform").glob("*.c")):
        code = path.read_text()
        assert "#include" in code

    # A second platform must be able to start from the contract alone.
    assert "RMB_PORT_PLATFORM_HEADER" in (REPO / "include/rmb/port.h").read_text()
    print(
        f"PASS portable resident is platform-free; {len(contract)} port functions "
        f"implemented by the ESP-IDF adapter"
    )


if __name__ == "__main__":
    main()
