"""Shared ownership and observation boundary for one Temper probe process."""

from __future__ import annotations

import json
import os
import datetime
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import watcher


SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class ProbeError(RuntimeError):
    """A probe process, identity, listener, or watcher invariant failed."""


def split_listen(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    try:
        port = int(port_text)
    except ValueError as error:
        raise ProbeError("probe listener port is invalid") from error
    if separator != ":" or host != "127.0.0.1" or not 1024 <= port <= 65535:
        raise ProbeError("probe listener must be an unprivileged IPv4 loopback address")
    return host, port


class _CaptureBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0
        self.exceeded = threading.Event()
        self.lock = threading.Lock()

    def take(self, count: int) -> int:
        with self.lock:
            accepted = min(count, max(0, self.maximum - self.used))
            self.used += accepted
            if accepted < count:
                self.exceeded.set()
            return accepted


def _pump_log(source: Any, destination: Path, budget: _CaptureBudget) -> None:
    with destination.open("wb") as output:
        while chunk := source.read(8192):
            accepted = budget.take(len(chunk))
            if accepted:
                output.write(chunk[:accepted])
        output.flush()
        os.fsync(output.fileno())


class _WatchHandle:
    def __init__(self, coordinated: watcher.CoordinatedWatcher) -> None:
        self.stop = threading.Event()
        self.result: dict[str, Any] | None = None
        self.error: BaseException | None = None

        def target() -> None:
            try:
                self.result = coordinated.run(self.stop)
            except BaseException as error:
                self.error = error

        self.thread = threading.Thread(target=target, name="field-kit-watcher", daemon=True)
        self.thread.start()

    def healthy(self) -> None:
        if self.error is not None:
            raise ProbeError(f"process watcher failed: {self.error}")
        if self.result is not None and self.result.get("state") != "complete":
            raise ProbeError("process watcher fired a safety stop")

    def finish(self) -> dict[str, Any]:
        self.stop.set()
        self.thread.join(timeout=45)
        if self.thread.is_alive():
            raise ProbeError("process watcher did not stop inside its bound")
        self.healthy()
        if self.result is None:
            raise ProbeError("process watcher returned no result")
        return self.result


def summarize_watch(path: Path, baseline_swap: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "samples": 0,
        "max_gap_milliseconds": 0,
        "max_sleep_wake_milliseconds": 0,
        "swap_baseline_bytes": baseline_swap,
        "swap_peak_bytes": baseline_swap,
        "swap_growth_bytes": 0,
        "thermal_warning_observed": False,
        "cpu_speed_limit_max": 0,
        "roles": {},
    }
    if not path.is_file():
        return summary
    for raw_line in path.read_bytes().splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("event") != "sample":
            continue
        summary["samples"] += 1
        summary["max_gap_milliseconds"] = max(
            summary["max_gap_milliseconds"], event.get("gap_milliseconds", 0)
        )
        summary["max_sleep_wake_milliseconds"] = max(
            summary["max_sleep_wake_milliseconds"],
            event.get("sleep_wake_milliseconds", 0),
        )
        snapshot = event.get("snapshot")
        if not isinstance(snapshot, dict):
            continue
        swap = snapshot.get("swap_used_bytes")
        if isinstance(swap, int):
            summary["swap_peak_bytes"] = max(summary["swap_peak_bytes"], swap)
            summary["swap_growth_bytes"] = max(0, summary["swap_peak_bytes"] - baseline_swap)
        summary["thermal_warning_observed"] = bool(
            summary["thermal_warning_observed"] or snapshot.get("thermal_warning")
        )
        speed = snapshot.get("cpu_speed_limit")
        if isinstance(speed, int):
            summary["cpu_speed_limit_max"] = max(summary["cpu_speed_limit_max"], speed)
        roles = snapshot.get("roles")
        if not isinstance(roles, dict):
            continue
        for role_id, record in roles.items():
            if not isinstance(record, dict):
                continue
            target = summary["roles"].setdefault(role_id, {
                "current_footprint_bytes_max": 0,
                "peak_footprint_bytes_max": 0,
                "rss_bytes_max": 0,
            })
            for key in target:
                value = record.get(key.removesuffix("_max"))
                if isinstance(value, int):
                    target[key] = max(target[key], value)
    return summary


class ManagedProbe:
    """Measure Temper-supplied identities and request foreground shutdown."""

    def __init__(self, *, temper, root, installation, execution_lock, generation,
                 listen, log_dir, watch_spec, router_ready_seconds, log_bytes_max):
        self.temper, self.root, self.installation = temper, root, installation
        self.execution_lock, self.generation, self.listen = execution_lock, generation, listen
        self.host, self.port = split_listen(listen)
        self.log_dir, self.watch_spec = log_dir, watch_spec
        watcher.validate_watch_spec(watch_spec)
        self.router_ready_seconds, self.log_bytes_max = router_ready_seconds, log_bytes_max
        self.status_path = log_dir / "temper-status.json"
        self.process = None
        self.router_binding = self.full_binding = None
        self.router_watch = self.full_watch = None
        self.log_threads = []
        self.log_budget = None
        self.baseline_swap = 0

    def _arguments(self):
        return [str(self.temper), "execution", "serve", "--root", str(self.root),
                "--installation", self.installation, "--lock", str(self.execution_lock),
                "--generation", self.generation, "--listen", self.listen,
                "--status-file", str(self.status_path)]

    def _status(self, *, final=False):
        if not self.status_path.exists():
            return None
        if self.status_path.is_symlink() or not self.status_path.is_file() or self.status_path.stat().st_size > 65536:
            raise ProbeError("Temper status is unsafe or oversized")
        raw = self.status_path.read_bytes()
        if not raw:
            return None
        try:
            status = json.loads(raw)
            expected = {"schema": "temper-probe-status/v1", "temper_pid": self.process.pid,
                        "root": str(self.root), "installation": self.installation,
                        "generation": self.generation, "listen": self.listen}
            if not isinstance(status, dict) or any(status.get(k) != v for k, v in expected.items()):
                raise ProbeError("Temper status does not bind this probe")
            if status.get("state") not in {"starting", "running", "stopped"}:
                raise ProbeError("Temper returned an unknown probe state")
            if type(status.get("safe_to_cleanup")) is not bool or type(status.get("listeners_verified")) is not bool:
                raise ProbeError("Temper returned an invalid shutdown or listener state")
            updated = datetime.datetime.fromisoformat(status["updated_at"].replace("Z", "+00:00"))
            age = (datetime.datetime.now(datetime.timezone.utc) - updated).total_seconds()
            if not final and not -1 <= age <= 15:
                raise ProbeError("Temper process observation is stale")
            return status
        except (ValueError, TypeError, KeyError) as error:
            raise ProbeError("Temper returned invalid probe status") from error

    def _binding(self, status, spec):
        roles = [r for r in status["roles"] if r["id"] in {r["id"] for r in spec["roles"]}]
        binding = {"schema": watcher.BINDING_SCHEMA,
                   "process_group_id": status["process_group_id"], "roles": roles}
        return watcher.validate_binding(spec, binding)

    def start(self):
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.status_path.exists() or self.status_path.is_symlink():
            raise ProbeError("probe status path already exists")
        self.baseline_swap = watcher.parse_swap_used_bytes(watcher.run_text([watcher.SYSCTL, "-n", "vm.swapusage"], 15))
        self.log_budget = _CaptureBudget(self.log_bytes_max)
        self.process = subprocess.Popen(self._arguments(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"PATH": SYSTEM_PATH})
        for source, name in ((self.process.stdout, "probe.stdout"), (self.process.stderr, "probe.stderr")):
            thread = threading.Thread(target=_pump_log, args=(source, self.log_dir / name, self.log_budget), daemon=True)
            thread.start()
            self.log_threads.append(thread)
        deadline = time.monotonic() + self.router_ready_seconds
        router_spec = {**self.watch_spec, "roles": [r for r in self.watch_spec["roles"] if r["id"] == "router"]}
        while time.monotonic() < deadline:
            self.ensure_healthy()
            status = self._status()
            if status and status["state"] == "running" and status["listeners_verified"]:
                self.router_binding = self._binding(status, router_spec)
                self.router_watch = _WatchHandle(watcher.CoordinatedWatcher(router_spec, self.router_binding,
                    self.log_dir / "router-watch.jsonl", baseline_swap_bytes=self.baseline_swap, request_stop=self.request_stop))
                return
            time.sleep(0.1)
        raise ProbeError("Temper did not establish the planned router boundary")

    def observe_engine(self):
        if self.full_watch is not None:
            return True
        status = self._status()
        if not status or not any(r["id"] == "engine" for r in status["roles"]):
            return False
        self.full_binding = self._binding(status, self.watch_spec)
        if self.router_watch is not None:
            self.router_watch.finish()
            self.router_watch = None
        self.full_watch = _WatchHandle(watcher.CoordinatedWatcher(self.watch_spec, self.full_binding,
            self.log_dir / "process-watch.jsonl", baseline_swap_bytes=self.baseline_swap, request_stop=self.request_stop))
        self.validate_owned_boundary()
        return True

    def ensure_healthy(self):
        if self.log_budget is not None and self.log_budget.exceeded.is_set():
            raise ProbeError("captured process logs reached their byte ceiling")
        if self.process is not None:
            if self.process.poll() is not None:
                raise ProbeError(f"Temper probe exited unexpectedly with status {self.process.returncode}")
            status = self._status()
            if status and (status.get("error") or status["state"] == "stopped"):
                raise ProbeError(status.get("error") or "Temper stopped the probe")
        for handle in (self.router_watch, self.full_watch):
            if handle is not None:
                handle.healthy()

    def validate_owned_boundary(self):
        status = self._status()
        if not status or not status["listeners_verified"]:
            raise ProbeError("Temper has not verified the probe listeners")
        if self.full_binding is not None and self._binding(status, self.watch_spec) != self.full_binding:
            raise ProbeError("Temper process identities changed during measurement")

    def request_stop(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass

    def finish(self):
        issues = []
        safe = self.process is None
        watchers_stopped = True
        for attribute in ("full_watch", "router_watch"):
            handle = getattr(self, attribute)
            if handle is not None:
                try:
                    handle.finish()
                except ProbeError as error:
                    issues.append(str(error))
                watchers_stopped = watchers_stopped and not handle.thread.is_alive()
                setattr(self, attribute, None)
        if self.process is not None:
            self.request_stop()
            try:
                self.process.wait(timeout=90)
                status = self._status(final=True)
                safe = bool(status and status["state"] == "stopped" and status["safe_to_cleanup"] is True)
                if status and status.get("error"):
                    issues.append(status["error"])
            except (OSError, subprocess.TimeoutExpired, ProbeError) as error:
                issues.append(str(error))
            if not safe:
                issues.append("Temper did not confirm owned process shutdown")
        for thread in self.log_threads:
            thread.join(timeout=10)
            if thread.is_alive():
                issues.append("process log reader did not stop")
                safe = False
        summary = summarize_watch(self.log_dir / "process-watch.jsonl", self.baseline_swap)
        summary.update(issues=issues, safe_to_cleanup=safe and watchers_stopped)
        return summary
