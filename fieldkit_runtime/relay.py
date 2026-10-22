"""Recording relay: a harness's HTTP requests reach the session's engine through Field Kit.

Request and response bodies pass unchanged. Each exchange keeps its bytes, wall
time, first byte, usage and the engine's native rates, so an external harness
gets per-request measurements and the record shows which model it called. The
relay listens on loopback and forwards only to the engine it was given.
"""
from __future__ import annotations
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time
from . import records
from .stream import ChatStream

BODY_LIMIT = 64 * 1024**2
HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade"}

def cut(channel):
    try:
        channel.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass

class _Events:
    """Server-sent events seen while forwarding; a parse problem stops reading, never forwarding."""
    def __init__(self, started, clock):
        self.stream = ChatStream(None, clock=clock)
        self.stream.started = started
        self.buffer, self.error = b"", None

    def feed(self, data):
        if self.error:
            return
        self.buffer += data
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith(b"data:"):
                continue
            payload = stripped[5:].strip()
            try:
                if payload == b"[DONE]":
                    self.stream.done = True
                else:
                    self.stream.event(json.loads(payload))
            except Exception as error:  # the stream stays the harness's; the record notes the gap
                self.error = f"{type(error).__name__}: {error}"[:300]
                return

class Relay:
    def __init__(self, host, port, folder: Path, engine, preset, timeout, *, clock=time.monotonic):
        self.upstream, self.folder, self.engine, self.preset = (host, port), Path(folder), engine, preset
        self.timeout, self.clock = timeout, clock
        self.rows, self.lock, self.number, self.open, self.closed = [], threading.Lock(), 0, set(), False
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = self.timeout  # a harness that stops reading cannot hold the relay open
            def log_message(self, *_):
                pass
            def do_GET(self):
                relay.exchange(self)
            do_POST = do_PUT = do_DELETE = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = False  # closing waits until every exchange is recorded
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        if self.closed:
            return False
        self.closed = True
        self.server.shutdown()
        with self.lock:  # the harness is gone: nothing may keep an engine request alive
            for channel in self.open:
                cut(channel)
        self.server.server_close()
        self.thread.join(timeout=5)
        records.write(self.folder / "index.json", self.summary())
        return False

    def exchange(self, handler):
        with self.lock:
            self.number += 1
            folder = self.folder / f"{self.number:04d}"
        folder.mkdir()
        started = self.clock()
        row = {"number": int(folder.name), "method": handler.command, "path": handler.path, "status": None,
               "model": None, "stream": False, "elapsed_seconds": None, "first_byte_seconds": None,
               "response_bytes": 0, "recorded_bytes": 0, "error": None}
        upstream = channel = None
        try:
            if "chunked" in (handler.headers.get("Transfer-Encoding") or "").lower():
                raise ValueError("chunked request bodies are not relayed; send Content-Length")
            size = int(handler.headers.get("Content-Length") or 0)
            if not 0 <= size <= BODY_LIMIT:
                raise ValueError("request body exceeds the relay bound")
            body = handler.rfile.read(size) if size else b""
            (folder / "request.body").write_bytes(body)
            try:
                sent = json.loads(body) if body else None
            except ValueError:
                sent = None
            if isinstance(sent, dict):
                row.update(model=sent.get("model"), stream=sent.get("stream") is True)
            headers = {name: value for name, value in handler.headers.items()
                       if name.lower() not in HOP | {"content-length", "host"}}
            # The relay keeps its own socket: http.client may hand the connection to the response.
            channel = socket.create_connection(self.upstream, self.timeout)
            with self.lock:
                self.open.add(channel)
            upstream = http.client.HTTPConnection(*self.upstream, timeout=self.timeout)
            upstream.sock = channel
            upstream.request(handler.command, handler.path, body if body or handler.command != "GET" else None, headers)
            response = upstream.getresponse()
            row["status"] = response.status
            kind = response.getheader("Content-Type", "")
            handler.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in HOP:
                    handler.send_header(name, value)
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.close_connection = True
            events = _Events(started, self.clock) if "event-stream" in kind or row["stream"] else None
            with (folder / "response.body").open("wb") as capture:
                while True:
                    data = response.read1(65536)
                    if not data:
                        break
                    if row["first_byte_seconds"] is None:
                        row["first_byte_seconds"] = self.clock() - started
                    row["response_bytes"] += len(data)
                    if row["recorded_bytes"] + len(data) <= BODY_LIMIT:
                        capture.write(data)
                        row["recorded_bytes"] += len(data)
                    if events:
                        events.feed(data)
                    try:
                        handler.wfile.write(data)
                        handler.wfile.flush()
                    except OSError:
                        row["error"] = "the harness closed the connection"
                        break
            row.update(self.measure(folder / "response.body", kind, events, row))
        except Exception as error:
            row["error"] = row["error"] or f"{type(error).__name__}: {error}"[:300]
            if row["status"] is None:
                try:
                    handler.send_error(502, "Field Kit relay could not reach the engine")
                except OSError:
                    pass
        finally:
            if channel:
                upstream.close()  # closes the socket: an abandoned request stops on the engine too
                with self.lock:
                    self.open.discard(channel)
            row["elapsed_seconds"] = self.clock() - started
            records.write(folder / "exchange.json", row)
            with self.lock:
                self.rows.append(row)

    def measure(self, path, kind, events, row):
        """Usage and native rates through the engine adapter, as for Field Kit's own requests."""
        if events:
            response = events.stream.snapshot()
            measured = {"first_token_seconds": response["first_token_seconds"], "finish_reason": response["finish_reason"],
                        "stream_complete": response["stream_complete"], "parse_error": events.error}
        elif "json" in kind and row["recorded_bytes"] == row["response_bytes"]:
            try:
                response = json.loads(path.read_bytes() or b"null")
            except ValueError:
                return {"parse_error": "response is not JSON"}
            if not isinstance(response, dict) or ("usage" not in response and "timings" not in response):
                return {}
            measured = {}
        else:
            return {}
        return {**measured, **self.engine.metrics(response)}

    def summary(self):
        rows = sorted(self.rows, key=lambda row: row["number"])
        models = {}
        for row in rows:
            if row["model"] is not None:
                models[row["model"]] = models.get(row["model"], 0) + 1
        generations = [row for row in rows if row["path"].split("?")[0].endswith("completions")]
        def total(key, partial=None):
            """A sum only when every generation request reported it; unknown stays unknown."""
            values = [row.get(key) if row.get(key) is not None else row.get(partial) if partial else None for row in generations]
            return sum(values) if all(value is not None for value in values) else None
        missing = sum(1 for row in generations if row.get("output_tokens") is None)
        return {"requests": len(rows), "generation_requests": len(generations), "models": models,
                "other_models": sorted(name for name in models if name != self.preset),
                "errors": sum(1 for row in rows if row["error"] or (row["status"] or 0) >= 400),
                "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
                "usage_missing": missing,
                "observed_output_tokens": total("output_tokens", "observed_output_tokens"),
                "observed_output_complete": missing == 0,
                "rows": rows}
