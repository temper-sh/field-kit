"""One Temper serve of the attempt's preset under resource watch.

Leaving the block always stops the serve and records resources and memory,
whatever happened inside. Failures propagate; they are never swallowed here.
"""
from __future__ import annotations
import socket
import time
from . import records
from .measurements import memory_record, watch_spec
from .probe import ProbeError

LOG_BYTES = 16 * 1024**2

def free_listen():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return "127.0.0.1:" + str(sock.getsockname()[1])

class EngineSession:
    def __init__(self, attempt, files):
        self.attempt, self.files = attempt, files
        self.context, self.engine, run = attempt.context, attempt.engine, attempt.run
        self.preset = run["execution"]["preset"]
        self.started = time.monotonic()
        self.startup_seconds = self.loaded = self.native = self.finished = None
        self.probe = self.context.probe(temper=attempt.temper.path, root=attempt.root,
            installation=run["installation"], execution_lock=attempt.files.lock,
            generation=run["preparation"]["generation"], listen=free_listen(), log_dir=files.process,
            watch_spec=watch_spec(run["resource_policy"], run["machine"], self.engine),
            router_ready_seconds=min(30, attempt.remaining(30)), log_bytes_max=LOG_BYTES)
        # Recovery compares this intent with Temper's final status before any cleanup.
        records.write(files.intent, {"root": str(attempt.root), "installation": run["installation"],
                                     "generation": run["preparation"]["generation"], "listen": self.probe.listen})

    @property
    def base_url(self):
        return f"http://{self.probe.listen}/v1"

    def __enter__(self):
        try:
            self.probe.start()
            self.context.call(self.probe, f"/upstream/{self.preset}{self.engine.health_path}", {},
                              self.attempt.remaining(1800), method="GET")
            if not self.probe.observe_engine():
                raise ProbeError("engine identity unavailable after readiness")
            self.startup_seconds = time.monotonic() - self.started
            self.loaded = self.context.sample(self.probe.full_binding, 15)
            self.native = self.engine.memory_observer(
                lambda path: self.context.call(self.probe, path, {}, 2, method="GET"), self.preset)
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
