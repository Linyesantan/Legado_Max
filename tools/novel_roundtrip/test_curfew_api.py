import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import curfew_api


class CurfewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config, self.state = root / 'config.json', root / 'state.json'
        self.config.write_text('{}')
        self.write_state(True)
        self.env = patch.dict('os.environ', CURFEW_CONFIG=str(self.config), CURFEW_STATE=str(self.state))
        self.env.start()
        self.provider = patch.object(curfew_api, 'policy_status', return_value=lambda config, state: state)
        self.provider.start()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not curfew_api.handle(self):
                    self.send_error(401)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.provider.stop()
        self.env.stop()
        self.temp.cleanup()

    def write_state(self, active):
        temp = self.state.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(active=active)))
        temp.replace(self.state)

    def fetch(self, path='/curfew/status?nonce=test'):
        with self.opener.open(self.base + path, timeout=6) as response:
            self.assertEqual('no-store', response.headers['Cache-Control'])
            return json.load(response)

    def test_active_and_minimal_public_payload(self):
        data = self.fetch('/novel/curfew/status?nonce=unique')
        self.assertEqual({'schema', 'active', 'available', 'lease_ms', 'revision', 'nonce'}, set(data))
        self.assertTrue(data['active'])
        self.assertTrue(data['available'])
        self.assertEqual('unique', data['nonce'])
        self.assertEqual(6000, data['lease_ms'])

    def test_early_release_wakes_waiting_app(self):
        initial = self.fetch()
        changed = threading.Timer(0.3, self.write_state, args=(False,))
        changed.start()
        start = time.monotonic()
        try:
            data = self.fetch('/curfew/status?revision=' + initial['revision'])
        finally:
            changed.join(timeout=2)
        self.assertFalse(data['active'])
        self.assertNotEqual(initial['revision'], data['revision'])
        self.assertLess(time.monotonic() - start, 1.5)

    def test_resume_curfew_wakes_waiting_app(self):
        self.write_state(False)
        initial = self.fetch()
        changed = threading.Timer(0.3, self.write_state, args=(True,))
        changed.start()
        try:
            self.assertTrue(self.fetch('/curfew/status?revision=' + initial['revision'])['active'])
        finally:
            changed.join(timeout=2)

    def test_unchanged_state_has_bounded_heartbeat(self):
        initial = self.fetch()
        with patch.object(curfew_api, 'POLL_SECONDS', 0.3):
            start = time.monotonic()
            data = self.fetch('/curfew/status?revision=' + initial['revision'])
        self.assertEqual(initial['revision'], data['revision'])
        self.assertGreaterEqual(time.monotonic() - start, 0.3)
        self.assertLess(time.monotonic() - start, 1.5)

    def test_missing_or_invalid_state_never_grants_lease(self):
        for text in (None, '{broken', '{"active":"false"}'):
            if text is None:
                self.state.unlink()
            else:
                self.state.write_text(text)
            data = self.fetch()
            self.assertTrue(data['active'])
            self.assertFalse(data['available'])
            self.assertEqual(0, data['lease_ms'])

    def test_policy_failure_does_not_expose_exception(self):
        self.provider.stop()
        self.provider = patch.object(curfew_api, 'policy_status', side_effect=RuntimeError('private path secret'))
        self.provider.start()
        data = self.fetch()
        self.assertFalse(data['available'])
        self.assertNotIn('secret', json.dumps(data))

    def test_status_route_cannot_bypass_other_routes(self):
        for path in ('/sources.json', '/curfew/status/other', '/curfew/../sources.json', '/novel/manuscript/test/1'):
            with self.assertRaises(HTTPError) as error:
                self.fetch(path)
            self.assertEqual(401, error.exception.code)


if __name__ == '__main__':
    unittest.main()
