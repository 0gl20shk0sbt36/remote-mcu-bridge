"""SDK-free structural checks only: does not compile or emulate ESP-IDF."""

from pathlib import Path
import csv
import re


def pyq(text):
    """Python-source audit view: quote style is not a contract."""
    return text.replace('"', "'")


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
source = (ROOT / "main/service.c").read_text()
business = (ROOT / "module/business.c").read_text()
backend = (ROOT / "module/platform/esp_idf_io.c").read_text()
config_files = list((ROOT / "module").rglob("Kconfig*"))
assert config_files, "STM service must own its own configuration"
config = "\n".join(p.read_text() for p in config_files)
base_config = (ROOT / "main/Kconfig.projbuild").read_text()
header = (ROOT.parent / "rom/rom.h").read_text()
rows = []
for row in csv.reader((ROOT / "partitions.csv").read_text().splitlines()):
    if not row or row[0].startswith("#"):
        continue
    name, kind, subtype, offset, size, *_ = row
    rows.append((name, kind, subtype, int(offset, 0), int(size, 0)))
last = 0x9000
for name, kind, subtype, offset, size in rows:
    assert offset >= last, f"partition overlap: {name}"
    assert offset % 4096 == size % 4096 == 0
    if kind == "app":
        assert offset % 65536 == 0
    last = offset + size
assert last <= 4 * 1024 * 1024
stage = next(r for r in rows if r[0] == "staging")
assert stage[2] == "0x40" and stage[3] == 0x110000 and stage[4] >= 1024 * 1024
assert next(r for r in rows if r[0] == "factory")[4] == 0x100000
assert len(b"STM32ROM_DISCOVER_V1") == 20
assert flat('sizeof("STM32ROM_DISCOVER_V1") - 1') in flat(source)
assert at(business, "supervisor_validate(device, &source") < at(
    business, "supervisor_download(&io, device"
)
assert at(business, "authenticated(token->valuestring)") < at(business, "esp_partition_erase_range")
assert flat("UART_PARITY_EVEN") in flat(backend) and flat("GPIO_MODE_INPUT_OUTPUT_OD") in flat(
    backend
)
assert flat("pin == 2 || pin == 8 || pin == 9 || (pin >= 12 && pin <= 17)") in flat(business)
assert flat("static int target_reset(void *ctx)") in flat(business)
assert flat("int (*reset)(void *);") in flat(header)
assert flat("return (int)total;") in flat(business) and flat("return done==ESP_OK?n:-1;") in flat(
    business
)
assert flat("device->image_max") in flat(business) and flat("device->flash_size") in flat(business)
assert flat("rom_device_f103c8") in flat(business) and flat("rom_device_f407zg") in flat(business)
assert flat("rom_device_") not in flat(source)
assert flat("config ROM_SERVICE_DEVICE_F103C8") in flat(config) and flat(
    "config ROM_SERVICE_DEVICE_F407ZG"
) in flat(config)
assert flat("#define REQUEST_MAX 1024") in flat(source)
assert re.search(r"config (?:RMB|ROM_SERVICE)_ENABLE\n.*\n\s+default n", base_config)
for name in ("TX", "RX", "BOOT0", "BOOT1", "NRST"):
    assert re.search(rf"config ROM_SERVICE_{name}\n.*\n    range -1 21\n    default -1", config)
assert re.search(r'config (?:RMB|ROM_SERVICE)_TOKEN\n.*\n\s+default ""', base_config)
assert '"module_runtime.c"' in (ROOT / "main/CMakeLists.txt").read_text()
assert '"../../rom/rom.c"' not in (ROOT / "main/CMakeLists.txt").read_text(), (
    "business ROM implementation belongs in independent XIP module, not resident base"
)
assert "rom/devices.c" not in (ROOT / "main/CMakeLists.txt").read_text()
assert "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y" in (ROOT / "sdkconfig.defaults").read_text()
print(
    "PASS: partition geometry, protocol/config safety invariants and current ROM callback contract"
)
assert at(business, "authenticated(token->valuestring)") < at(
    business, 'if (!strcmp(op->valuestring, "probe"))'
)
assert flat("uart_driver_install(UART_PORT, (int)rx_size, 0, 64, events, 0)") in flat(backend)
assert flat("esp_io_uart_init(&uart_events,TARGET_RX_BUFFER_SIZE)") in flat(business)
assert all(
    x in business
    for x in ("UART_FRAME_ERR", "UART_PARITY_ERR", "UART_FIFO_OVF", "UART_BUFFER_FULL")
)
assert flat("pending<sizeof target_snapshot?pending:sizeof target_snapshot") in flat(business)
assert flat("first_byte_task_observed_us") in flat(business)
core = (ROOT.parent / "rom/rom.c").read_text()
probe = core[at(core, "static int probe(") : at(core, "int rom_restart(")]
assert (
    flat("cmd(i,0)") in flat(probe)
    and flat("cmd(i,2)") in flat(probe)
    and flat("get_version(i,r->version,v)") in flat(probe)
)
tx = business[at(business, "static int target_tx(") : at(business, "static int target_rx(")]
reset = business[
    at(business, "static int target_reset(") : at(business, "static void target_sleep(")
]
prepare = business[
    at(business, "static int target_prepare(") : at(business, "static void target_trace(")
]
assert flat("if(d && !d->prepared)") in flat(tx) and flat("d->probe") not in flat(tx)
assert flat("if(length!=1 || data[0]!=0x7f) return -1;") in flat(tx)
assert at(tx, "target_prepare(d)") < at(tx, "uart_write_bytes(")
assert flat("uart_flush_input") not in flat(reset) and flat("d->prepared=false") in flat(reset)
assert flat("uart_flush_input(") not in flat(business) and flat("uart_flush_input(") not in flat(
    source
)
assert flat('target_snapshot_drain(d,"pre-sync")') in flat(prepare) and flat(
    "Pre-sync UART errors:"
) in flat(prepare)
assert flat("d->frame_errors=d->parity_errors=d->fifo_overflows=d->buffer_full=0") in flat(prepare)
assert all(
    x in business
    for x in (
        "version-options",
        "version-zero",
        "version-mismatch",
        "version_queried",
        "version_mismatch",
    )
)
assert not any(x in probe for x in ("erase_image(", "cmd(i,0x31)", "cmd(i,0x21)", "cmd(i,0x92)"))
assert flat("rom_probe(") not in flat(source)[at(source, "void app_main(void)") :]
# The resident base owns networking/management, not target UART/GPIO state.
for call in (
    "uart_driver_install(",
    "uart_param_config(",
    "uart_set_pin(",
    "gpio_config(",
    "gpio_set_level(",
    "gpio_set_pull_mode(",
):
    assert call in business + backend, f"business backend missing hardware owner: {call}"
    assert call not in source, f"resident base owns target hardware: {call}"
assert flat('#include "driver/uart.h"') in flat(business)
assert flat('#include "driver/gpio.h"') in flat(business)
assert flat("module_runtime_dispatch") not in flat(source)
assert flat("module_ops") not in flat(business)
entry = (ROOT / "module/entry.c").read_text()
assert flat("RMB_NET_AUTH_REPLAY") in flat(entry)
assert flat("3333") in flat(entry) and flat("3336") in flat(entry)
# Import resident addresses without copying their state; bind to the final ELF.
builder = (ROOT / "module/build_module.py").read_text()
assert pyq('"-Wl,--just-symbols=" + str(base_elf)') in pyq(builder)
assert pyq("base_hash = hashlib.sha256(base_elf.read_bytes()).hexdigest()") in pyq(builder)
assert pyq("data = bytes.fromhex(base_hash) + section('data')[32:]") in pyq(builder)
assert pyq("if hashlib.sha256(base_elf.read_bytes()).hexdigest() != base_hash:") in pyq(builder)
assert builder.index(
    "if hashlib.sha256(base_elf.read_bytes()).hexdigest() != base_hash:"
) < builder.index("out.write_bytes(image)")
runtime = (ROOT / "main/module_runtime.c").read_text()
assert flat("memcmp(binding,esp_app_get_description()->app_elf_sha256,sizeof binding)") in flat(
    runtime
)
assert flat("module_runtime_update_allowed()") in flat(runtime)
assert flat("module_ops") not in flat(runtime)
print(
    "PASS: business UART/GPIO ownership, resident generic dispatch, just-symbols imports and final-base ELF hash binding"
)
print(
    "PASS: authenticated explicit-only probe, read-only command whitelist, full bounded stale snapshots and UART telemetry"
)
print(
    "NOT CHECKED: ESP-IDF compilation, driver semantics, Wi-Fi/TCP/UDP runtime, physical wiring or STM32 programming"
)
