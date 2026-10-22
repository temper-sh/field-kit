"""One engine process started directly from a local declaration, without Temper.

Field Kit binds the process it started, watches it like a Temper serve and
proves shutdown itself: the process group is gone and the port is closed. Its
status file plays the role of Temper's status for recovery.
"""
from __future__ import annotations
import datetime
import os
import signal
import socket
import subprocess
import threading
import time
from . import watcher
from .common import canonical_json
from .probe import STATUS_FILE, ProbeError, _CaptureBudget, _pump_log, _WatchHandle, split_listen, summarize_watch
from .process import atomic_write

LOCAL_STATUS_SCHEMA = "field-kit-local-status/v1"
IDENTITY_CHECK_SECONDS = 5

def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False

class LocalProbe:
    """The same interface as ManagedProbe, for an engine Field Kit starts itself."""

    def __init__(self, *, argv, environment, listen, log_dir, watch_spec, log_bytes_max, intent, stop_seconds=30):
        self.argv, self.environment, self.listen = list(argv), dict(environment), listen
        self.host, self.port = split_listen(listen)
        self.log_dir, self.log_bytes_max, self.stop_seconds = log_dir, log_bytes_max, stop_seconds
        # A local engine is one process; Temper's router and helper roles do not exist here.
        self.watch_spec = {**watch_spec, "roles": [role for role in watch_spec["roles"] if role["id"] == "engine"]}
        watcher.validate_watch_spec(self.watch_spec)
        self.intent = dict(intent)
        self.status_path = log_dir / STATUS_FILE
        self.process = self.identity = self.full_binding = self.full_watch = self.log_budget = None
        self.log_threads = []
        self.baseline_swap, self.baseline_wired = 0, None
        self.checked_at = 0.0

    def _status(self, state, safe, error=None):
        document = {"schema": LOCAL_STATUS_SCHEMA, **self.intent, "state": state, "safe_to_cleanup": safe,
                    "process": self.identity, "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        if error:
            document["error"] = error
        atomic_write(self.status_path, canonical_json(document))

    def start(self):
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.baseline_swap = watcher.parse_swap_used_bytes(watcher.run_text([watcher.SYSCTL, "-n", "vm.swapusage"], 15))
        self.baseline_wired = watcher.parse_wired_bytes(watcher.run_text([watcher.VM_STAT], 15))
        self.log_budget = _CaptureBudget(self.log_bytes_max)
        self._status("starting", False)
        self.process = subprocess.Popen(self.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env=self.environment, start_new_session=True)
        for source, name in ((self.process.stdout, "engine.stdout"), (self.process.stderr, "engine.stderr")):
            thread = threading.Thread(target=_pump_log, args=(source, self.log_dir / name, self.log_budget), daemon=True)
            thread.start()
            self.log_threads.append(thread)
        try:
            self.identity = watcher.read_process_identity(self.process.pid, 15)
        except Exception as error:
            raise ProbeError(f"local engine exited before Field Kit could bind it: {error}") from error
        if self.identity["pgid"] != self.process.pid:
            raise ProbeError("local engine does not lead its own process group")
        self._status("running", False)
        self.full_binding = watcher.validate_binding(self.watch_spec, {
            "schema": watcher.BINDING_SCHEMA, "process_group_id": self.identity["pgid"],
            "roles": [{"id": "engine", **self.identity}]})
        self.full_watch = _WatchHandle(watcher.CoordinatedWatcher(self.watch_spec, self.full_binding,
            self.log_dir / "process-watch.jsonl", baseline_swap_bytes=self.baseline_swap,
            baseline_wired_bytes=self.baseline_wired, request_stop=self.request_stop))

    def observe_engine(self):
        return self.full_watch is not None

    def ensure_healthy(self):
        if self.log_budget is not None and self.log_budget.exceeded.is_set():
            raise ProbeError("captured engine logs reached their byte ceiling")
        if self.process is not None and self.process.poll() is not None:
            raise ProbeError(f"local engine exited unexpectedly with status {self.process.returncode}")
        if self.full_watch is not None:
            self.full_watch.healthy()

    def validate_owned_boundary(self):
        """The bound process is still the one Field Kit started. Re-read at most every few seconds."""
        if self.identity is None:
            raise ProbeError("local engine is not bound")
        if time.monotonic() - self.checked_at < IDENTITY_CHECK_SECONDS:
            return
        try:
            current = watcher.read_process_identity(self.process.pid, 15)
        except Exception as error:
            raise ProbeError(f"local engine identity unavailable: {error}") from error
        if current != self.identity:
            raise ProbeError("local engine process identity changed during measurement")
        self.checked_at = time.monotonic()

    def request_stop(self):
        # Until Field Kit reaps it, the child's PID cannot be reused, and it leads its own group.
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def finish(self):
        issues, watchers_stopped = [], True
        if self.full_watch is not None:
            try:
                self.full_watch.finish()
            except ProbeError as error:
                issues.append(str(error))
            watchers_stopped = not self.full_watch.thread.is_alive()
            self.full_watch = None
        safe = self.process is None or self._stop(issues)
        for thread in self.log_threads:
            thread.join(timeout=10)
            if thread.is_alive():
                issues.append("engine log reader did not stop")
                safe = False
        if self.process is not None and not any(thread.is_alive() for thread in self.log_threads):
            self.process.stdout.close()
            self.process.stderr.close()
        self._status("stopped" if safe else "retained", safe, "; ".join(issues) or None)
        summary = summarize_watch(self.log_dir / "process-watch.jsonl", self.baseline_swap, self.baseline_wired)
        summary.update(issues=issues, safe_to_cleanup=safe and watchers_stopped)
        return summary

    def _stop(self, issues):
        """SIGTERM the group, wait, then SIGKILL the group Field Kit created. Proof: group gone, port closed."""
        group = self.process.pid
        self.request_stop()
        try:
            self.process.wait(timeout=self.stop_seconds)
        except subprocess.TimeoutExpired:
            pass
        if group_alive(group):
            try:
                os.killpg(group, signal.SIGKILL)
                issues.append("engine group outlived SIGTERM; Field Kit killed the group it created")
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                issues.append("engine did not exit after SIGKILL")
            deadline = time.monotonic() + 5
            while group_alive(group) and time.monotonic() < deadline:
                time.sleep(0.1)
        if group_alive(group):
            issues.append("engine process group still exists")
            return False
        if port_open(self.host, self.port):
            issues.append("engine port is still open")
            return False
        return self.process.poll() is not None
