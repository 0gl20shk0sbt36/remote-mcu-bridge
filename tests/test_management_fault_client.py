#!/usr/bin/env python3
"""Actual management client over localhost TCP; synthetic peer, no MCU claim."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rmb_management_client', ROOT/'tools/module_ctl.py')
assert spec and spec.loader
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)
TOKEN = 'PUBLIC_TEST_TOKEN_NOT_SECRET'

@contextlib.contextmanager
def peer(reply):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(3)
    state = {'port': listener.getsockname()[1], 'requests': [], 'errors': []}
    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(3)
                with connection.makefile('rb') as stream:
                    raw = stream.readline(4096)
                    assert raw.endswith(b'\n')
                    state['requests'].append(json.loads(raw))
                    connection.sendall(json.dumps(reply).encode()+b'\n')
        except BaseException as error:
            state['errors'].append(error)
        finally:
            listener.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        thread.join(4)
        assert not thread.is_alive(), 'mock management peer did not finish'
        if state['errors']:
            raise state['errors'][0]

class ManagementFaultClient(unittest.TestCase):
    def test_get_fault_does_not_ack(self):
        report = {'ok': True, 'seq': 7, 'reason': 1, 'role': 'control', 'checkpoint': 42}
        with peer(report) as server:
            result = ctl.request('127.0.0.1', server['port'], TOKEN, 'get-fault', timeout=2)
        self.assertEqual(result, report)
        self.assertEqual(server['requests'], [{'token': TOKEN, 'op': 'get-fault'}])

    def test_ack_is_explicit_and_sequence_bound(self):
        with peer({'ok': True}) as server:
            ctl.request('127.0.0.1', server['port'], TOKEN, 'ack-fault', timeout=2, fault_seq=7)
        self.assertEqual(server['requests'], [{'token': TOKEN, 'op': 'ack-fault', 'seq': 7}])

    def test_stale_ack_remains_error(self):
        with peer({'ok': False, 'error': 'stale fault sequence'}) as server:
            with self.assertRaisesRegex(RuntimeError, 'stale fault sequence'):
                ctl.request('127.0.0.1', server['port'], TOKEN, 'ack-fault', timeout=2, fault_seq=6)

    def test_invalid_sequence_rejected_before_network(self):
        with patch.object(ctl.socket, 'create_connection', side_effect=AssertionError('unexpected network')):
            for seq in (None, 0, -1, True, 1.0, '7', 0x100000000):
                with self.subTest(seq=seq), self.assertRaises(ValueError):
                    ctl.request('unused', 3337, TOKEN, 'ack-fault', fault_seq=seq)
            with self.assertRaises(ValueError):
                ctl.request('unused', 3337, TOKEN, 'status', fault_seq=7)

    def test_cli_get_fault(self):
        with tempfile.TemporaryDirectory(prefix='rmb-fault-client-') as work:
            token = Path(work)/'token.txt'
            token.write_text(TOKEN)
            output = io.StringIO()
            with peer({'ok': True, 'seq': 9, 'reason': 1}) as server, contextlib.redirect_stdout(output):
                status = ctl.main(['get-fault', '--host', '127.0.0.1', '--port', str(server['port']), '--token-file', str(token)])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())['seq'], 9)
            self.assertNotIn(TOKEN, output.getvalue())

    def test_cli_ack_requires_sequence_and_no_unrelated_sequence(self):
        cases = [
            ['ack-fault', '--host', 'unused'],
            ['ack-fault', '--host', 'unused', '--fault-seq', '0'],
            ['status', '--host', 'unused', '--fault-seq', '7'],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    ctl.main(arguments)
                self.assertEqual(error.exception.code, 2)

if __name__ == '__main__':
    unittest.main()
