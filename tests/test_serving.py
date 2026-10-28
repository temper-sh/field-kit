"""A fast response does not lose its attempt to a late engine record.

Failure meaning: Temper writes the engine role into its status shortly after the
router starts the engine. Without a grace period, a quick first response failed
the whole attempt as "no observed bound engine" (seen on Granite 4.2 3B with
thinking on, 2026-10-28), although the engine was bound a moment later.
"""
import http.server
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from fieldkit_runtime import serving
from fieldkit_runtime.probe import ProbeError

class BindGraceTest(unittest.TestCase):
    def setUp(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_port

    def probe(self, bound_after):
        calls = []
        def observe():
            calls.append(1)
            return len(calls) > bound_after
        return SimpleNamespace(host="127.0.0.1", port=self.port, ensure_healthy=lambda: None,
                               observe_engine=observe, validate_owned_boundary=lambda: None)

    def test_a_late_engine_record_is_awaited(self):
        self.assertEqual(serving.monitored_call(self.probe(bound_after=5), "/health", {}, 5, method="GET"), {"ok": True})

    def test_an_engine_that_never_binds_still_fails(self):
        with mock.patch.object(serving, "BIND_GRACE_SECONDS", 0.3), self.assertRaisesRegex(ProbeError, "no observed bound engine"):
            serving.monitored_call(self.probe(bound_after=10**6), "/health", {}, 5, method="GET")

if __name__ == "__main__":
    unittest.main()
