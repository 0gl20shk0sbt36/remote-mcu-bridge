"""Host checks: execute actual C digest parser; audit supervisor contracts.
Not an ESP32 emulator; no hardware/watchdog/flash claims.
"""
import ctypes
import csv
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'main/supervisor.inc').read_text()
SERVICE = (ROOT / 'main/service.c').read_text()
BUSINESS = (ROOT / 'module/business.c').read_text()


class SupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        start = SOURCE.index('static bool supervisor_hex(')
        end = SOURCE.index('static void supervisor_serve(', start)
        c = '#include <stdint.h>\n#include <stdbool.h>\n#include <string.h>\n'
        c += SOURCE[start:end].replace('static bool supervisor_hex', 'bool supervisor_hex')
        p = Path(cls.tmp.name)
        (p / 'parser.c').write_text(c)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', str(p / 'parser.c'), '-o', str(p / 'parser.so')], check=True)
        cls.lib = ctypes.CDLL(str(p / 'parser.so'))
        cls.lib.supervisor_hex.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint8)]
        cls.lib.supervisor_hex.restype = ctypes.c_bool

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_digest_roundtrip(self):
        rng = random.Random(15)
        for _ in range(1000):
            data = bytes(rng.randrange(256) for _ in range(32))
            for text in (data.hex(), data.hex().upper()):
                out = (ctypes.c_uint8 * 32)()
                self.assertTrue(self.lib.supervisor_hex(text.encode(), out))
                self.assertEqual(bytes(out), data)

    def test_digest_malformed(self):
        out = (ctypes.c_uint8 * 32)()
        for size in range(130):
            if size != 64:
                self.assertFalse(self.lib.supervisor_hex(b'0' * size, out))
        for pos in range(64):
            for bad in (b'g', b'/', b':', b' ', b'\n', b'\xff'):
                text = bytearray(b'0' * 64)
                text[pos] = bad[0]
                self.assertFalse(self.lib.supervisor_hex(bytes(text), out))
        self.assertFalse(self.lib.supervisor_hex(None, out))

    def test_partition(self):
        rows = {r[0]: r for r in csv.reader((ROOT / 'partitions.csv').read_text().splitlines()) if r and not r[0].startswith('#')}
        self.assertEqual(rows['business'][1:5], ['data', '0x41', '0x220000', '0x100000'])
        self.assertEqual(rows['staging'][3:5], ['0x110000', '0x110000'])
        self.assertEqual(rows['factory'][3:5], ['0x10000', '0x100000'])

    def test_resident_contracts(self):
        serve = SOURCE[SOURCE.index('static void supervisor_serve('):SOURCE.index('static void supervisor_management(')]
        self.assertLess(serve.index('authenticated(token->valuestring)'), serve.index('module_runtime_install_begin(size)'))
        self.assertLess(serve.index('control_acquire()'), serve.index('module_runtime_install_begin(size)'))
        self.assertLess(serve.index('module_runtime_install_begin(size)'), serve.index('module_runtime_install_write('))
        self.assertLess(serve.index('module_runtime_install_write('), serve.index('module_runtime_install_finish('))
        self.assertIn('seen!=15', serve)
        self.assertIn('seen!=3', serve)
        self.assertIn('UPLOAD_US', serve)
        self.assertIn('module_runtime_invalidate()', serve)
        self.assertIn('RTC_NOINIT_ATTR', SOURCE)
        self.assertIn('rmb_supervisor_record_write(&supervisor_rtc', SOURCE)
        self.assertIn('.timeout_ms=20000', SOURCE)
        self.assertIn('if(module_allowed_this_boot && !supervisor_fault && supervisor_start())', SOURCE)
        self.assertIn('xSemaphoreTake(job->done,portMAX_DELAY)', SOURCE)
        self.assertIn('.trigger_panic=true', SOURCE)
        self.assertIn('for(;;) vTaskDelay(1)', SOURCE)
        self.assertIn('if(owned) control_release()', SOURCE)
        for call in ('rom_validate(', 'rom_download_traced(', 'rom_probe(', 'rom_restart('):
            self.assertNotIn('= '+call, SERVICE)
        self.assertIn('if (remaining > 250000)', BUSINESS)
        self.assertIn('supervisor_status(fd)', BUSINESS)

    def test_xip_activation_contracts(self):
        setup = SOURCE[SOURCE.index('static void supervisor_init(void)'):]
        self.assertIn('module_runtime_prepare()', SERVICE)
        self.assertLess(SERVICE.index('module_runtime_prepare()'), SERVICE.index('nvs_flash_init()'))
        self.assertLess(SERVICE.index('module_runtime_prepare()'), SERVICE.index('esp_wifi_init(&init)'))
        self.assertNotIn('module_runtime_init()', setup)
        self.assertNotIn('supervisor_submit(', setup)
        manager = SOURCE[SOURCE.index('static void supervisor_management('):SOURCE.index('static void supervisor_monitor(')]
        self.assertLess(manager.index('listen(server,2)'), manager.index('supervisor_start()'))
        main = SERVICE[SERVICE.index('void app_main(void)'):]
        self.assertLess(main.index('supervisor_init()'), main.index('xTaskCreate(supervisor_management'))
        start = SOURCE[SOURCE.index('static int supervisor_start('):SOURCE.index('static void supervisor_status(')]
        self.assertIn('static supervisor_job init_job', start)
        self.assertNotIn('xSemaphoreTake', start)
        self.assertIn('if(job->done) xSemaphoreGive(job->done)', SOURCE)
        for field in ('text_address', 'data_address', 'module_abi', 'reboot_required'):
            self.assertIn(field, SOURCE)

    def test_cli_enter_update(self):
        import json
        import socket
        import threading
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            observed = []
            def serve():
                conn, _ = server.accept()
                with conn, conn.makefile('rb') as stream:
                    observed.append(json.loads(stream.readline()))
                    conn.sendall(b'{"ok":true,"reboot_required":true}\n')
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            env = dict(os.environ, STM32_ROM_TOKEN='native-cli-test-token')
            result = subprocess.run([sys.executable, str(ROOT.parent / 'tools/module_ctl.py'),
                                     'enter-update', '--host', '127.0.0.1', '--port',
                                     str(server.getsockname()[1])], env=env,
                                    text=True, capture_output=True, timeout=5)
            worker.join(timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'ok': True, 'reboot_required': True})
            self.assertEqual(observed, [{'token': 'native-cli-test-token', 'op': 'enter-update'}])

    def test_actual_hung_entry_management(self):
        subprocess.run([sys.executable, str(ROOT / 'tests/test_supervisor_startup.py')], check=True, timeout=15)


if __name__ == '__main__':
    unittest.main()
