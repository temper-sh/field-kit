"""Bounded HTTP calls to a supervised probe while its processes stay healthy."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time

from .probe import ProbeError


RESPONSE_LIMIT = 4 * 1024**2


class HTTPResponseError(ProbeError):
    """The HTTP endpoint rejected a request or exceeded its response bound."""


def monitored_call(probe, path: str, payload: dict, timeout: float, *, streaming=False, stream_reader=None, method="POST", observer=None):
    if timeout <= 0:
        raise TimeoutError("approved time budget exhausted")
    if streaming and stream_reader is None:
        raise ValueError("a streaming call needs a stream reader")
    connection = http.client.HTTPConnection(probe.host, probe.port, timeout=timeout)
    results, failures, sockets = [], [], []
    done = threading.Event()
    started = time.monotonic()

    def worker():
        try:
            connection.request(method, path, json.dumps(payload) if method == "POST" else None, {"Content-Type": "application/json"})
            if connection.sock is not None:
                sockets.append(connection.sock)
            response = connection.getresponse()
            if response.status != 200:
                raise HTTPResponseError(f"model HTTP status {response.status}: {response.read(512)!r}")
            if streaming:
                result = stream_reader(response, started)
            else:
                raw = response.read(RESPONSE_LIMIT + 1)
                if len(raw) > RESPONSE_LIMIT:
                    raise HTTPResponseError("response exceeded 4 MiB")
                result = json.loads(raw) if raw else {}
            results.append(result)
        except Exception as error:
            failures.append(error)
        finally:
            connection.close()
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while not done.wait(0.1):
            probe.ensure_healthy()
            probe.observe_engine()
            if time.monotonic() - started > timeout:
                raise TimeoutError("request reached the approved time limit")
            if observer is not None:
                observer()
        probe.ensure_healthy()
        if not probe.observe_engine():
            raise ProbeError("response has no observed bound engine")
        probe.validate_owned_boundary()
        if failures:
            raise failures[0]
        return results[0]
    finally:
        for active_socket in sockets:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()
        thread.join(timeout=2)
