"""SDK-free structural checks only: does not compile or emulate ESP-IDF."""
from pathlib import Path
import csv
import re

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "main/service.c").read_text()
business = (ROOT / "module/business.c").read_text()
backend = (ROOT / "module/platform/esp_idf_io.c").read_text()
config = (ROOT / "main/Kconfig.projbuild").read_text()
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
assert 'sizeof("STM32ROM_DISCOVER_V1") - 1' in source
assert business.index("supervisor_validate(device, &source") < business.index("supervisor_download(&io, device")
assert business.index("authenticated(token->valuestring)") < business.index("esp_partition_erase_range")
assert "UART_PARITY_EVEN" in backend and "GPIO_MODE_INPUT_OUTPUT_OD" in backend
assert "pin == 2 || pin == 8 || pin == 9 || (pin >= 12 && pin <= 17)" in business
assert "static int target_reset(void *ctx)" in business
assert "int (*reset)(void *);" in header
assert "return (int)total;" in business and "return done==ESP_OK?n:-1;" in business
assert "device->image_max" in business and "device->flash_size" in business
assert "rom_device_f103c8" in source and "rom_device_f407zg" in source
assert "config ROM_SERVICE_DEVICE_F103C8" in config and "config ROM_SERVICE_DEVICE_F407ZG" in config
assert "#define REQUEST_MAX 1024" in source
assert 'config ROM_SERVICE_ENABLE\n    bool' in config
assert re.search(r"config ROM_SERVICE_ENABLE\n.*\n    default n", config)
for name in ("TX", "RX", "BOOT0", "BOOT1", "NRST"):
    assert re.search(rf"config ROM_SERVICE_{name}\n.*\n    range -1 21\n    default -1", config)
assert re.search(r'config ROM_SERVICE_TOKEN\n.*\n    default ""', config)
assert '"module_runtime.c"' in (ROOT / "main/CMakeLists.txt").read_text()
assert '"../../rom/rom.c"' not in (ROOT / "main/CMakeLists.txt").read_text(), 'business ROM implementation belongs in independent XIP module, not resident base'
assert '"../../rom/devices.c"' in (ROOT / "main/CMakeLists.txt").read_text()
assert 'CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y' in (ROOT / "sdkconfig.defaults").read_text()
print("PASS: partition geometry, protocol/config safety invariants and current ROM callback contract")
assert business.index("authenticated(token->valuestring)") < business.index('if (!strcmp(op->valuestring, "probe"))')
assert 'uart_driver_install(UART_PORT, (int)rx_size, 0, 64, events, 0)' in backend
assert 'esp_io_uart_init(&uart_events,TARGET_RX_BUFFER_SIZE)' in business
assert all(x in business for x in ('UART_FRAME_ERR','UART_PARITY_ERR','UART_FIFO_OVF','UART_BUFFER_FULL'))
assert 'pending<sizeof target_snapshot?pending:sizeof target_snapshot' in business
assert 'first_byte_task_observed_us' in business
core = (ROOT.parent / 'rom/rom.c').read_text()
probe = core[core.index('static int probe('):core.index('int rom_restart(')]
assert 'cmd(i,0)' in probe and 'cmd(i,2)' in probe and 'get_version(i,r->version,v)' in probe
tx = business[business.index('static int target_tx('):business.index('static int target_rx(')]
reset = business[business.index('static int target_reset('):business.index('static void target_sleep(')]
prepare = business[business.index('static int target_prepare('):business.index('static void target_trace(')]
assert 'if(d && !d->prepared)' in tx and 'd->probe' not in tx
assert 'if(length!=1 || data[0]!=0x7f) return -1;' in tx
assert tx.index('target_prepare(d)') < tx.index('uart_write_bytes(')
assert 'uart_flush_input' not in reset and 'd->prepared=false' in reset
assert 'uart_flush_input(' not in business and 'uart_flush_input(' not in source
assert 'target_snapshot_drain(d,"pre-sync")' in prepare and 'Pre-sync UART errors:' in prepare
assert 'd->frame_errors=d->parity_errors=d->fifo_overflows=d->buffer_full=0' in prepare
assert all(x in business for x in ('version-options','version-zero','version-mismatch','version_queried','version_mismatch'))
assert not any(x in probe for x in ('erase_image(', 'cmd(i,0x31)', 'cmd(i,0x21)', 'cmd(i,0x92)'))
assert 'rom_probe(' not in source[source.index('void app_main(void)'):]
# The resident base owns networking/management, not target UART/GPIO state.
for call in ('uart_driver_install(', 'uart_param_config(', 'uart_set_pin(',
             'gpio_config(', 'gpio_set_level(', 'gpio_set_pull_mode('):
    assert call in business + backend, f'business backend missing hardware owner: {call}'
    assert call not in source, f'resident base owns target hardware: {call}'
assert '#include "driver/uart.h"' in business
assert '#include "driver/gpio.h"' in business
assert 'module_runtime_dispatch(1,&fd)' in source
assert 'module_runtime_dispatch(2,&fd)' in (ROOT / 'main/streams.inc').read_text()
assert 'int module_business_dispatch(uint32_t channel,void *argument)' in business
assert 'if(channel==1)' in business and 'if(channel==2)' in business
# Import resident addresses without copying their state; bind to the final ELF.
builder = (ROOT / 'module/build_module.py').read_text()
assert "'-Wl,--just-symbols=' + str(base_elf)" in builder
assert 'base_hash = hashlib.sha256(base_elf.read_bytes()).hexdigest()' in builder
assert "data = bytes.fromhex(base_hash) + section('data')[32:]" in builder
assert 'if hashlib.sha256(base_elf.read_bytes()).hexdigest() != base_hash:' in builder
assert builder.index('if hashlib.sha256(base_elf.read_bytes()).hexdigest() != base_hash:') < builder.index('out.write_bytes(image)')
runtime = (ROOT / 'main/module_runtime.c').read_text()
assert 'memcmp(binding,esp_app_get_description()->app_elf_sha256,sizeof binding)' in runtime
assert 'if(!module_runtime_update_allowed()||ops||busy)return ESP_ERR_INVALID_STATE;' in runtime
print('PASS: business UART/GPIO ownership, resident generic dispatch, just-symbols imports and final-base ELF hash binding')
print('PASS: authenticated explicit-only probe, read-only command whitelist, full bounded stale snapshots and UART telemetry')
print("NOT CHECKED: ESP-IDF compilation, driver semantics, Wi-Fi/TCP/UDP runtime, physical wiring or STM32 programming")
