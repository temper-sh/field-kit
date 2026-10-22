"""One serve of the attempt's subject under resource watch: a Temper serve of a
preset, or a local engine Field Kit starts itself.

Leaving the block always stops the serve and records resources and memory,
whatever happened inside. Failures propagate; they are never swallowed here.
"""
from __future__ import annotations
import socket
import time
from .measurements import memory_record
from .probe import ProbeError

def free_listen():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return "127.0.0.1:" + str(sock.getsockname()[1])

class EngineSession:
    def __init__(self, attempt, files):
        self.attempt, self.files = attempt, files
        self.context, self.engine, self.subject = attempt.context, attempt.engine, attempt.subject
        # The model name requests carry: the preset ID behind Temper's router, or the local engine's own.
        self.preset = self.subject.model
        self.started = time.monotonic()
        self.startup_seconds = self.loaded = self.native = self.finished = None
        self.probe = self.subject.probe(files, free_listen())

    @property
    def base_url(self):
        return f"http://{self.probe.listen}/v1"

    @property
    def native_url(self):
        """The engine's own routes (props, template, tokenizer), whichever subject serves it."""
        return f"http://{self.probe.listen}{self.subject.native_prefix}"

    def __enter__(self):
        try:
            self.probe.start()
            self.subject.ready(self)
            if not self.probe.observe_engine():
                raise ProbeError("engine identity unavailable after readiness")
            self.startup_seconds = time.monotonic() - self.started
            self.loaded = self.context.sample(self.probe.full_binding, 15)
            self.native = self.engine.memory_observer(
                lambda path: self.context.call(self.probe, path, {}, 2, method="GET"), self.subject.native_prefix)
            if self.native:
                self.native.capture("loaded")
        except BaseException:
            self.finish()
            raise
        return self

    def __exit__(self, *exception):
        self.finish()
        return False

    def call(self, path, body, timeout, **options):
        return self.context.call(self.probe, path, body, timeout,
                                 observer=self.native.poll if self.native else None, **options)

    def observe(self):
        """Between harness polls: the serve is healthy, owned and still the same processes."""
        self.probe.ensure_healthy()
        self.probe.validate_owned_boundary()
        if self.native:
            self.native.poll()

    def finish(self):
        if self.finished is not None:
            return self.finished
        try:
            if self.native:
                self.native.capture("final")
        except (Exception, KeyboardInterrupt):
            pass
        resources = self.probe.finish()
        self.finished = {"resources": resources, "safe_to_cleanup": resources.get("safe_to_cleanup", False),
            "memory": memory_record(self.loaded, resources, self.native.measurements() if self.native else None),
            "elapsed_seconds": time.monotonic() - self.started}
        return self.finished
