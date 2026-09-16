"""Shared ownership and observation boundary for one Temper probe process."""

from __future__ import annotations

import json
import os
import socket
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


def listener_accepting(host: str, port: int, timeout: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        return connection.connect_ex((host, port)) == 0


def parse_process_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        try:
            pid, ppid, pgid = (int(parts[index]) for index in range(3))
        except ValueError:
            continue
        rows.append({"pid": pid, "ppid": ppid, "pgid": pgid, "command": parts[3]})
    return rows


def process_rows() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,pgid=,comm="],
        capture_output=True,
        check=False,
        env={"PATH": SYSTEM_PATH},
        timeout=15,
    )
    if completed.returncode:
        raise ProbeError("could not read the process tree")
    return parse_process_rows(completed.stdout.decode(errors="replace"))


def process_name(row: dict[str, Any]) -> str:
    return Path(str(row["command"])).name


def find_single_process(
    rows: list[dict[str, Any]],
    *,
    name: str,
    ppid: int | None = None,
    pgid: int | None = None,
) -> dict[str, Any] | None:
    matches = [
        row for row in rows
        if process_name(row) == name
        and (ppid is None or row["ppid"] == ppid)
        and (pgid is None or row["pgid"] == pgid)
    ]
    if len(matches) > 1:
        raise ProbeError(f"more than one {name} process matched the owned group")
    return matches[0] if matches else None


def validate_group_rows(rows: list[dict[str, Any]], pgid: int) -> list[dict[str, Any]]:
    members = [row for row in rows if row["pgid"] == pgid]
    allowed = {"bash", "llama-server", "llama-swap", "sh"}
    unexpected = [process_name(row) for row in members if process_name(row) not in allowed]
    if unexpected:
        raise ProbeError("unexpected process in owned group: " + ", ".join(sorted(unexpected)))
    return members


def listener_records(pids: list[int]) -> list[dict[str, Any]]:
    if not pids:
        return []
    completed = subprocess.run(
        [
            "/usr/sbin/lsof", "-nP", "-a", "-p",
            ",".join(str(pid) for pid in sorted(pids)),
            "-iTCP", "-sTCP:LISTEN", "-Fpn",
        ],
        capture_output=True,
        check=False,
        env={"PATH": SYSTEM_PATH},
        timeout=15,
    )
    if completed.returncode not in {0, 1}:
        raise ProbeError("could not inspect owned listeners")
    records: list[dict[str, Any]] = []
    current: int | None = None
    for line in completed.stdout.decode(errors="replace").splitlines():
        if line.startswith("p") and line[1:].isdigit():
            current = int(line[1:])
        elif line.startswith("n") and current is not None:
            records.append({"pid": current, "name": line[1:]})
    return records


def validate_listeners(records: list[dict[str, Any]], router_pid: int, listen: str) -> None:
    planned = [record for record in records if record["name"].endswith(listen)]
    if len(planned) != 1 or planned[0]["pid"] != router_pid:
        raise ProbeError("planned listener is absent or not owned by the router")
    for record in records:
        endpoint = record["name"]
        if not (endpoint.startswith("127.0.0.1:") or endpoint.startswith("[::1]:")):
            raise ProbeError(f"owned process exposed a non-loopback listener: {endpoint}")


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
    """Start, bind, watch, and stop one exact Temper probe process group."""

    def __init__(
        self,
        *,
        temper: Path,
        root: Path,
        installation: str,
        software_lock: Path,
        generation: str,
        listen: str,
        log_dir: Path,
        watch_spec: dict[str, Any],
        router_ready_seconds: int,
        log_bytes_max: int,
    ) -> None:
        self.temper = temper
        self.root = root
        self.installation = installation
        self.software_lock = software_lock
        self.generation = generation
        self.listen = listen
        self.host, self.port = split_listen(listen)
        self.log_dir = log_dir
        self.watch_spec = watch_spec
        watcher.validate_watch_spec(watch_spec)
        self.router_ready_seconds = router_ready_seconds
        self.log_bytes_max = log_bytes_max
        self.process: subprocess.Popen[bytes] | None = None
        self.router_binding: dict[str, Any] | None = None
        self.full_binding: dict[str, Any] | None = None
        self.router_watch: _WatchHandle | None = None
        self.full_watch: _WatchHandle | None = None
        self.log_threads: list[threading.Thread] = []
        self.log_budget: _CaptureBudget | None = None
        self.baseline_swap = 0

    def _arguments(self, dry: bool = False) -> list[str]:
        result = [
            str(self.temper), "probe", "serve", "--root", str(self.root),
            "--installation", self.installation,
            "--software-lock", str(self.software_lock),
            "--generation", self.generation, "--listen", self.listen,
        ]
        if dry:
            result.append("--dry-run")
        return result

    def _wait(self, predicate: Any, deadline: float, message: str) -> None:
        while time.monotonic() < deadline:
            self.ensure_healthy()
            if predicate():
                return
            time.sleep(0.1)
        raise ProbeError(message)

    def start(self) -> None:
        if listener_accepting(self.host, self.port):
            raise ProbeError("dedicated listener is already in use")
        dry = subprocess.run(
            self._arguments(True), capture_output=True, check=False,
            env={"PATH": SYSTEM_PATH}, timeout=30,
        )
        if dry.returncode or b"RESULT probe-serve ready-to-start" not in dry.stdout:
            raise ProbeError("Temper refused the exact probe invocation")
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.baseline_swap = watcher.parse_swap_used_bytes(
            watcher.run_text([watcher.SYSCTL, "-n", "vm.swapusage"], 15)
        )
        self.log_budget = _CaptureBudget(self.log_bytes_max)
        self.process = subprocess.Popen(
            self._arguments(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": SYSTEM_PATH},
        )
        assert self.process.stdout is not None and self.process.stderr is not None
        for source, name in (
            (self.process.stdout, "probe.stdout"),
            (self.process.stderr, "probe.stderr"),
        ):
            thread = threading.Thread(
                target=_pump_log,
                args=(source, self.log_dir / name, self.log_budget),
                daemon=True,
            )
            thread.start()
            self.log_threads.append(thread)
        router: dict[str, Any] | None = None

        def found() -> bool:
            nonlocal router
            assert self.process is not None
            router = find_single_process(process_rows(), name="llama-swap", ppid=self.process.pid)
            return router is not None

        deadline = time.monotonic() + self.router_ready_seconds
        self._wait(found, deadline, "owned llama-swap process did not appear")
        assert router is not None
        if router["pgid"] != router["pid"]:
            raise ProbeError("router does not own its dedicated process group")
        router_spec = {**self.watch_spec, "roles": [self.watch_spec["roles"][1]]}
        self.router_binding = watcher.bind_registration(
            router_spec,
            {
                "schema": "field-kit-process-registration/v1",
                "process_group_id": router["pgid"],
                "roles": [{"id": "router", "pid": router["pid"]}],
            },
        )
        self.router_watch = _WatchHandle(watcher.CoordinatedWatcher(
            router_spec,
            self.router_binding,
            self.log_dir / "router-watch.jsonl",
            baseline_swap_bytes=self.baseline_swap,
        ))
        self._wait(
            lambda: listener_accepting(self.host, self.port), deadline,
            "router did not open the planned listener",
        )
        validate_listeners(listener_records([router["pid"]]), router["pid"], self.listen)

    def observe_engine(self) -> bool:
        if self.full_watch is not None:
            return True
        if self.router_binding is None:
            raise ProbeError("router is not bound")
        engine = find_single_process(
            process_rows(), name="llama-server",
            pgid=self.router_binding["process_group_id"],
        )
        if engine is None:
            return False
        router = self.router_binding["roles"][0]
        registration = {
            "schema": "field-kit-process-registration/v1",
            "process_group_id": self.router_binding["process_group_id"],
            "roles": [
                {"id": "engine", "pid": engine["pid"]},
                {"id": "router", "pid": router["pid"]},
            ],
        }
        self.full_binding = watcher.bind_registration(self.watch_spec, registration)
        self.full_watch = _WatchHandle(watcher.CoordinatedWatcher(
            self.watch_spec,
            self.full_binding,
            self.log_dir / "process-watch.jsonl",
            baseline_swap_bytes=self.baseline_swap,
        ))
        if self.router_watch is not None:
            self.router_watch.finish()
            self.router_watch = None
        self.validate_owned_boundary()
        return True

    def ensure_healthy(self) -> None:
        if self.log_budget is not None and self.log_budget.exceeded.is_set():
            raise ProbeError("captured process logs reached their byte ceiling")
        if self.process is not None and self.process.poll() is not None:
            raise ProbeError(f"Temper probe exited unexpectedly with status {self.process.returncode}")
        if self.router_watch is not None:
            self.router_watch.healthy()
        if self.full_watch is not None:
            self.full_watch.healthy()

    def validate_owned_boundary(self) -> None:
        if self.full_binding is None:
            return
        rows = validate_group_rows(process_rows(), self.full_binding["process_group_id"])
        names = [process_name(row) for row in rows]
        if names.count("llama-swap") != 1 or names.count("llama-server") != 1:
            raise ProbeError("owned group no longer has exactly one router and engine")
        router_pid = next(
            role["pid"] for role in self.full_binding["roles"] if role["id"] == "router"
        )
        validate_listeners(listener_records([row["pid"] for row in rows]), router_pid, self.listen)

    def finish(self) -> dict[str, Any]:
        issues: list[str] = []
        shutdown_issues: list[str] = []
        for attribute in ("full_watch", "router_watch"):
            handle = getattr(self, attribute)
            if handle is not None:
                try:
                    handle.finish()
                except ProbeError as error:
                    issues.append(str(error))
                    if handle.thread.is_alive():
                        shutdown_issues.append(str(error))
                setattr(self, attribute, None)
        observation_issues = len(issues)
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                binding = self.full_binding or self.router_binding
                if binding is not None:
                    try:
                        watcher.terminate_bound_group(
                            binding, 30, command_timeout_seconds=15
                        )
                    except Exception as error:
                        issues.append(f"identity-bound termination failed: {error}")
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    issues.append("Temper probe did not exit after bound termination")
        for thread in self.log_threads:
            thread.join(timeout=10)
            if thread.is_alive():
                issues.append("process log reader did not stop")
        deadline = time.monotonic() + 30
        while listener_accepting(self.host, self.port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if listener_accepting(self.host, self.port):
            issues.append("planned listener remained after shutdown")
        binding = self.full_binding or self.router_binding
        if binding is not None:
            try:
                if any(row["pgid"] == binding["process_group_id"] for row in process_rows()):
                    issues.append("owned process group still has members after shutdown")
            except ProbeError as error:
                issues.append(f"could not verify final process shutdown: {error}")
        elif self.process is not None:
            issues.append("probe started without a verified process-group binding")
        summary = summarize_watch(self.log_dir / "process-watch.jsonl", self.baseline_swap)
        summary["issues"] = issues
        # An observed safety stop does not imply the owned processes remain alive.
        # Keep experiment validity separate from permission to remove their files.
        shutdown_issues.extend(issues[observation_issues:])
        summary["safe_to_cleanup"] = not shutdown_issues
        return summary
