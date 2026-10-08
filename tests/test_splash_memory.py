"""Memory evidence retains native peaks, bounded failures and engine identity."""
import http.server
import json
import threading
import unittest
from types import SimpleNamespace

from fieldkit_runtime.catalog import Refusal
from fieldkit_runtime.experiments.qwen.measurement import HTTPResponseError, monitored_call
from fieldkit_runtime.experiments.qwen.splash_memory import SplashMemory, report_lines, review
from fieldkit_runtime.probe import ProbeError

GIB = 1024**3
SOURCE = "/upstream/splash-q4/status"


def status(current=20, peak=22, instance="first", restarts=0):
    return {"ready": True, "instance": {"id": instance, "pid": 1234},
            "transport": {"ready": True, "recovering": False, "status_stale": False, "restarts": restarts},
            "memory_actual": {"current_bytes": current * GIB, "peak_bytes": peak * GIB}}


class SplashMemoryTest(unittest.TestCase):
    def test_load_and_native_lifetime_peak_survive_reclamation_and_poll_interval(self):
        readings = iter([status(20, 22), status(24, 29), status(18, 29)])
        now = [0]
        memory = SplashMemory(lambda: next(readings), SOURCE, 5, clock=lambda: now[0])
        memory.capture("loaded")
        now[0] = 4
        memory.poll()
        now[0] = 5
        memory.poll()
        memory.capture("final")

        record = memory.result()
        self.assertEqual(record["samples"], 3)
        self.assertEqual(record["loaded"]["current_bytes"], 20 * GIB)
        self.assertEqual(record["last"]["current_bytes"], 18 * GIB)
        self.assertEqual(record["last"]["peak_bytes"], 29 * GIB)
        self.assertEqual(record["state"], "observed")
        review(record, "splash-q4")
        row = {"id": "q4", "kind": "coding", "cases": [{"id": "async-stream"}], "resources": [{"native_memory": record}]}
        self.assertIn("| q4 | async-stream | 20.00 | 29.00 | observed |", report_lines([row]))

    def test_missing_or_malformed_counters_never_become_zero_usage(self):
        malformed = [{}, {"ready": False}, {**status(), "memory_actual": {}}]
        for key in ("recovering", "status_stale"):
            item = status()
            item["transport"][key] = True
            malformed.append(item)
        for key in ("current_bytes", "peak_bytes"):
            for invalid in (None, True, -1, "10", float("nan")):
                item = status()
                item["memory_actual"][key] = invalid
                malformed.append(item)
        malformed.append(status(30, 20))
        for value in malformed:
            with self.subTest(value=value):
                memory = SplashMemory(lambda: value, SOURCE, 5)
                memory.capture("loaded")
                memory.capture("final")
                record = memory.result()
                self.assertEqual(record["state"], "unmeasured")
                self.assertIsNone(record["last"])
                review(record, "splash-q4")

    def test_failed_final_read_retains_a_partial_peak_without_erasing_load(self):
        def read():
            if memory.record["samples"]:
                raise TimeoutError("native status deadline")
            return status()
        memory = SplashMemory(read, SOURCE, 5)
        memory.capture("loaded")
        memory.capture("final")

        record = memory.result()
        self.assertEqual(record["state"], "partial")
        self.assertFalse(record["finished"])
        self.assertEqual(record["loaded"]["current_bytes"], 20 * GIB)
        row = {"id": "q4", "kind": "coding", "cases": [], "resources": [{"native_memory": record}]}
        self.assertIn("| q4 | coding | 20.00 | ≥22.00 | partial |", report_lines([row]))
        review(record, "splash-q4")

    def test_changed_instance_or_reset_cannot_combine_two_engines_peaks(self):
        for replacement in (status(20, 21), status(20, 40, instance="second"), status(20, 40, restarts=1)):
            with self.subTest(replacement=replacement):
                readings = iter([status(), replacement])
                memory = SplashMemory(lambda: next(readings), SOURCE, 5)
                memory.capture("loaded")
                memory.capture("request")
                memory.capture("final")
                record = memory.result()
                self.assertEqual(record["state"], "invalid")
                self.assertEqual(record["last"]["peak_bytes"], 22 * GIB)
                review(record, "splash-q4")

    def test_witness_rejects_changed_counter_state_or_source(self):
        memory = SplashMemory(status, SOURCE, 5)
        memory.capture("loaded")
        memory.capture("final")
        for change in ({"source": "/upstream/splash-q5/status"}, {"samples": 0},
                       {"finished": False}, {"issues": ["lost observation"]}):
            with self.subTest(change=change), self.assertRaises(Refusal):
                review({**memory.result(), **change}, "splash-q4")
        record = memory.result()
        record["last"]["peak_bytes"] = 19 * GIB
        with self.assertRaises(Refusal):
            review(record, "splash-q4")

    def test_optional_status_errors_do_not_swallow_process_safety_stops(self):
        def rejected():
            raise HTTPResponseError("status endpoint unavailable")
        memory = SplashMemory(rejected, SOURCE, 5)
        memory.capture("loaded")
        self.assertEqual(memory.result()["state"], "unmeasured")

        def unsafe():
            raise ProbeError("Temper process identities changed during measurement")
        memory = SplashMemory(unsafe, SOURCE, 5)
        with self.assertRaisesRegex(ProbeError, "identities changed"):
            memory.capture("request")
        self.assertEqual(memory.result()["state"], "invalid")

    def test_status_is_polled_while_a_real_http_response_is_streaming(self):
        polled = threading.Event()
        readings = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                readings.append(self.path)
                body = json.dumps(status(20 if len(readings) == 1 else 24, 22 if len(readings) == 1 else 29)).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if len(readings) > 1:
                    polled.set()

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n')
                self.wfile.flush()
                if not polled.wait(3):
                    return
                self.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                self.wfile.flush()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        probe = SimpleNamespace(host="127.0.0.1", port=server.server_port,
                                ensure_healthy=lambda: None, observe_engine=lambda: True,
                                validate_owned_boundary=lambda: None)
        memory = SplashMemory(lambda: monitored_call(probe, SOURCE, {}, 1, method="GET"), SOURCE, 0.01)
        memory.capture("loaded")

        response = monitored_call(probe, "/v1/chat/completions", {}, 3, streaming=True, observer=memory.poll)
        memory.capture("final")

        self.assertTrue(response["stream_complete"])
        self.assertTrue(polled.is_set())
        self.assertEqual(memory.result()["state"], "observed")
        self.assertEqual(memory.result()["last"]["peak_bytes"], 29 * GIB)
        self.assertGreaterEqual(len(readings), 3)
        self.assertEqual(set(readings), {SOURCE})
