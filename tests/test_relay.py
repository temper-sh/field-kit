"""The recording relay changes no bytes and records what a harness actually asked for.

Failure meanings: changed bytes would alter the harness's evidence; a missing
rate or model would hide what was measured; an unclosed upstream would leave
the engine generating for a harness that gave up.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from fieldkit_runtime.adapters import engines
from fieldkit_runtime.relay import Relay

def sse(*events):
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"

STREAM = sse({"choices": [{"index": 0, "delta": {"reasoning_content": "think"}}]},
             {"choices": [{"index": 0, "delta": {"content": "OK"}}]},
             {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 12, "completion_tokens": 2},
              "timings": {"prompt_n": 12, "prompt_ms": 6, "prompt_per_second": 2000.0,
                          "predicted_n": 2, "predicted_ms": 50, "predicted_per_second": 40.0}})
PROPS = json.dumps({"default_generation_settings": {"n_ctx": 4096}}).encode()

class Engine:
    """A fake engine whose answers are fixed bytes; it notes when a client goes away."""
    def __init__(self):
        self.abandoned = threading.Event()
        engine = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def reply(self, status, kind, data, chunks=1):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                step = max(1, len(data) // chunks)
                for start in range(0, len(data), step):
                    self.wfile.write(data[start:start + step])
                    self.wfile.flush()
                    time.sleep(0.01)
            def do_GET(self):
                self.reply(200, "application/json", PROPS)
            def do_POST(self):
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/v1/chat/completions":
                    self.reply(200, "text/event-stream", STREAM, chunks=5)
                elif self.path == "/json":
                    self.reply(200, "application/json", json.dumps({"usage": {"prompt_tokens": 7}, "data": []}).encode())
                elif self.path == "/garbage":
                    self.reply(200, "text/event-stream", b"data: {not json\n\n" + STREAM)
                elif self.path == "/forever":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    try:
                        for _ in range(500):
                            self.wfile.write(b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "x"}}]}).encode() + b"\n\n")
                            self.wfile.flush()
                            time.sleep(0.01)
                    except OSError:
                        engine.abandoned.set()
                else:
                    self.reply(400, "text/plain", b"input exceeds the context size")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

class RelayTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "relay"
        self.engine = Engine()
        self.addCleanup(self.engine.close)
        self.relay = Relay("127.0.0.1", self.engine.server.server_port, self.folder,
                           engines.resolve("llama-server/v1"), "preset-a", 10)
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)

    def send(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.relay.server.server_port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, data, {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        result = response.status, response.getheader("Content-Length"), response.read()
        connection.close()
        return result

    def row(self, number):
        self.relay.__exit__(None, None, None)  # waits until every exchange is recorded
        return json.loads((self.folder / f"{number:04d}/exchange.json").read_text())

    def test_a_stream_passes_unchanged_and_yields_native_rates(self):
        body = {"model": "preset-a", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        status, _, data = self.send("POST", "/v1/chat/completions", body)
        self.assertEqual((status, data), (200, STREAM))
        self.assertEqual((self.folder / "0001/response.body").read_bytes(), STREAM)
        self.assertEqual(json.loads((self.folder / "0001/request.body").read_bytes()), body)
        row = self.row(1)
        self.assertEqual((row["model"], row["stream"], row["finish_reason"], row["stream_complete"]), ("preset-a", True, "stop", True))
        self.assertEqual((row["input_tokens"], row["output_tokens"]), (12, 2))
        self.assertEqual((row["prefill_tokens_per_second"], row["generation_tokens_per_second"]), (2000.0, 40.0))
        self.assertLessEqual(row["first_byte_seconds"], row["first_token_seconds"])
        self.assertLess(row["first_token_seconds"], row["elapsed_seconds"])

    def test_json_and_native_routes_pass_with_their_length(self):
        status, length, data = self.send("GET", "/upstream/preset-a/props")
        self.assertEqual((status, int(length), data), (200, len(PROPS), PROPS))
        status, _, data = self.send("POST", "/json", {"model": "preset-a"})
        self.assertEqual(json.loads(data)["usage"]["prompt_tokens"], 7)
        self.assertIsNone(self.row(1)["model"])
        self.assertEqual(self.row(2)["input_tokens"], 7)

    def test_engine_errors_and_other_models_are_visible(self):
        status, _, data = self.send("POST", "/missing", {"model": "another-model"})
        self.assertEqual((status, data), (400, b"input exceeds the context size"))
        self.relay.__exit__(None, None, None)
        summary = json.loads((self.folder / "index.json").read_text())
        self.assertEqual((summary["errors"], summary["other_models"]), (1, ["another-model"]))

    def test_an_unreachable_engine_is_a_bad_gateway(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            closed = sock.getsockname()[1]
        relay = Relay("127.0.0.1", closed, self.folder.parent / "other", engines.resolve("llama-server/v1"), "preset-a", 2)
        with relay:
            connection = http.client.HTTPConnection("127.0.0.1", relay.server.server_port, timeout=10)
            connection.request("POST", "/v1/chat/completions", b"{}", {"Content-Type": "application/json"})
            self.assertEqual(connection.getresponse().status, 502)
            connection.close()
        self.assertIn("ConnectionRefusedError", relay.summary()["rows"][0]["error"])

    def test_a_broken_event_never_breaks_forwarding(self):
        status, _, data = self.send("POST", "/garbage", {"model": "preset-a", "stream": True})
        self.assertEqual(data, b"data: {not json\n\n" + STREAM)
        self.assertIn("JSONDecodeError", self.row(1)["parse_error"])

    def test_an_abandoned_request_stops_on_the_engine(self):
        body = json.dumps({"model": "preset-a", "stream": True}).encode()
        with socket.create_connection(("127.0.0.1", self.relay.server.server_port), timeout=10) as client:
            client.sendall(b"POST /forever HTTP/1.1\r\nHost: relay\r\nContent-Type: application/json\r\n"
                           b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            self.assertIn(b"data:", client.recv(4096) + client.recv(4096))
        self.assertTrue(self.engine.abandoned.wait(5))
        self.assertEqual(self.row(1)["error"], "the harness closed the connection")

    def test_closing_the_relay_cuts_a_request_still_running(self):
        body = json.dumps({"model": "preset-a", "stream": True}).encode()
        client = socket.create_connection(("127.0.0.1", self.relay.server.server_port), timeout=10)
        self.addCleanup(client.close)
        client.sendall(b"POST /forever HTTP/1.1\r\nHost: relay\r\nContent-Type: application/json\r\n"
                       b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        client.recv(4096)
        started = time.monotonic()
        self.relay.__exit__(None, None, None)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(self.engine.abandoned.wait(5))
        self.assertEqual(json.loads((self.folder / "index.json").read_text())["requests"], 1)

if __name__ == "__main__":
    unittest.main()
