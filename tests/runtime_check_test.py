"""Read-only startup diagnostics exercised with real local sockets and HTTP responses."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('runtime_check', Path(__file__).resolve().parents[1] / 'scripts/runtime-check.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeCheckTest(unittest.TestCase):
    def test_occupied_port_is_rejected_without_stopping_the_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(ValueError, 'existing processes will not be stopped'):
                runtime.port_available(port)
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                pass
        runtime.port_available(port)

    def test_invalid_launch_configuration_is_rejected_before_side_effects(self):
        for port in ['0', '65536', '-1', 'localhost:8080', '8080/path']:
            with self.assertRaises(ValueError):
                runtime.configuration({'DJOUND_PORT': port})
        with self.assertRaisesRegex(ValueError, 'DJOUND_MODE'):
            runtime.configuration({'DJOUND_MODE': 'AUTO_LIVE'})
        self.assertEqual((18093, 'FIXTURE'), runtime.configuration({'DJOUND_PORT': '18093', 'DJOUND_MODE': 'FIXTURE'}))
        with self.assertRaisesRegex(ValueError, 'LIVE is retired'):
            runtime.configuration({'DJOUND_MODE': 'LIVE'})
        self.assertEqual((8080, 'LOCAL'), runtime.configuration({'DJOUND_MODE': 'LOCAL'}))

    def test_status_uses_only_local_read_and_rejects_a_different_service(self):
        requests = []
        payload = []
        redirect = False

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.command, self.path))
                if redirect and self.path == '/api/workspace':
                    self.send_response(302)
                    self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/unexpected')
                    self.end_headers()
                    return
                encoded = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(ValueError, 'not responding'):
                runtime.status(server.server_port)
            payload = {'mode': 'LIVE', 'activeAttemptId': None,
                       'submissionAllowance': {'remaining': 0, 'used': 4}}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                runtime.status(server.server_port)
            self.assertIn('0 remaining, 4 spent', output.getvalue())
            self.assertIn('does not confirm provider capacity', output.getvalue())
            payload = {'mode': 'LIVE', 'submissionAllowance': {}}
            with self.assertRaisesRegex(ValueError, 'not responding'):
                runtime.status(server.server_port)
            payload = {'mode': 'UNCONFIGURED', 'submissionAllowance': None}
            redirect = True
            with self.assertRaisesRegex(ValueError, 'not responding'):
                runtime.status(server.server_port)
            self.assertEqual([('GET', '/api/workspace')] * 4, requests)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_status_deadline_stops_a_slow_trickle_without_waiting_for_socket_timeout(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '1000')
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            with patch.object(runtime, 'STATUS_DEADLINE_SECONDS', 0.3):
                with self.assertRaisesRegex(ValueError, 'not responding'):
                    runtime.status(server.server_port)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
