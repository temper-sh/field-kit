"""Shared coordinated macOS process observation and safety termination."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from threading import Event
from typing import Any, Callable, Sequence

from .catalog import IDENTITY, Refusal


WATCH_SCHEMA = "field-kit-process-watch/v1"
REGISTRATION_SCHEMA = "field-kit-process-registration/v1"
BINDING_SCHEMA = "field-kit-process-binding/v1"
EVENT_SCHEMA = "field-kit-process-watch-event/v1"
FOOTPRINT = "/usr/bin/footprint"
PS = "/bin/ps"
SYSCTL = "/usr/sbin/sysctl"
PMSET = "/usr/bin/pmset"
MIB = 1024**2


class WatchFailure(RuntimeError):
    """A required counter or frozen process identity could not be verified."""


CommandReader = Callable[[Sequence[str], float], str]
IdentityReader = Callable[[int, float], dict[str, Any]]


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Refusal(f"{label} must be a positive integer")
    return value


def _optional_limit(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _positive_integer(value, label)


def validate_watch_spec(value: object) -> dict[str, Any]:
    fields = {
        "schema", "interval_milliseconds", "max_gap_milliseconds",
        "command_timeout_seconds", "term_grace_seconds", "roles",
        "swap_growth_bytes_max", "thermal_stop", "cpu_speed_limit_stop",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise Refusal("process watch has missing or unknown fields")
    if value.get("schema") != WATCH_SCHEMA:
        raise Refusal("process watch has an unsupported schema")
    interval = _positive_integer(
        value.get("interval_milliseconds"),
        "process watch interval_milliseconds",
    )
    maximum_gap = _positive_integer(
        value.get("max_gap_milliseconds"),
        "process watch max_gap_milliseconds",
    )
    if maximum_gap < interval:
        raise Refusal("process watch maximum gap must not be shorter than its interval")
    _positive_integer(
        value.get("command_timeout_seconds"),
        "process watch command_timeout_seconds",
    )
    _positive_integer(
        value.get("term_grace_seconds"),
        "process watch term_grace_seconds",
    )
    _optional_limit(value.get("swap_growth_bytes_max"), "process watch swap limit")
    if not isinstance(value.get("thermal_stop"), bool) or not isinstance(
        value.get("cpu_speed_limit_stop"), bool
    ):
        raise Refusal("process watch thermal and CPU-speed policies must be booleans")
    roles = value.get("roles")
    if not isinstance(roles, list) or not roles:
        raise Refusal("process watch requires at least one process role")
    previous = ""
    for role in roles:
        if not isinstance(role, dict) or set(role) != {
            "id", "rss_bytes_max", "current_footprint_bytes_max",
            "peak_footprint_bytes_max",
        }:
            raise Refusal("process watch role has missing or unknown fields")
        role_id = role.get("id")
        if (
            not isinstance(role_id, str)
            or not IDENTITY.fullmatch(role_id)
            or role_id <= previous
        ):
            raise Refusal("process watch roles must have sorted unique IDs")
        previous = role_id
        for name in (
            "rss_bytes_max",
            "current_footprint_bytes_max",
            "peak_footprint_bytes_max",
        ):
            _optional_limit(role.get(name), f"process watch role {role_id!r} {name}")
    return value


def run_text(arguments: Sequence[str], timeout_seconds: float) -> str:
    """Run one exact read-only system command without a shell."""
    try:
        completed = subprocess.run(
            list(arguments),
            capture_output=True,
            check=False,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise WatchFailure(f"required counter command failed: {arguments[0]}") from error
    if completed.returncode != 0:
        raise WatchFailure(f"required counter command exited {completed.returncode}: {arguments[0]}")
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WatchFailure(f"required counter command returned non-UTF-8: {arguments[0]}") from error


def parse_process_identity(pgid_text: str, start_text: str, pid: int) -> dict[str, Any]:
    pgid_match = re.fullmatch(r"\s*([0-9]+)\s*", pgid_text)
    started = " ".join(start_text.split())
    if not pgid_match or int(pgid_match.group(1)) <= 0 or not started:
        raise WatchFailure("could not parse exact process identity")
    return {
        "pid": pid,
        "pgid": int(pgid_match.group(1)),
        "ps_lstart": started,
    }


def read_process_identity(
    pid: int,
    timeout_seconds: float,
    command: CommandReader = run_text,
) -> dict[str, Any]:
    pgid = command([PS, "-o", "pgid=", "-p", str(pid)], timeout_seconds)
    started = command([PS, "-o", "lstart=", "-p", str(pid)], timeout_seconds)
    return parse_process_identity(pgid, started, pid)


def bind_registration(
    spec: dict[str, Any],
    registration: object,
    identity_reader: IdentityReader = read_process_identity,
) -> dict[str, Any]:
    """Freeze exact PIDs, one explicit group, and each role's start identity."""
    validate_watch_spec(spec)
    if not isinstance(registration, dict) or set(registration) != {
        "schema", "process_group_id", "roles",
    }:
        raise Refusal("process registration has missing or unknown fields")
    if registration.get("schema") != REGISTRATION_SCHEMA:
        raise Refusal("process registration has an unsupported schema")
    pgid = _positive_integer(
        registration.get("process_group_id"),
        "process registration process_group_id",
    )
    if pgid == os.getpgrp():
        raise Refusal("process registration cannot name Field Kit's own process group")
    raw_roles = registration.get("roles")
    if not isinstance(raw_roles, list):
        raise Refusal("process registration roles must be a list")
    expected_ids = [role["id"] for role in spec["roles"]]
    if [role.get("id") for role in raw_roles if isinstance(role, dict)] != expected_ids:
        raise Refusal("process registration roles differ from the watch declaration")
    timeout = float(spec["command_timeout_seconds"])
    bound_roles: list[dict[str, Any]] = []
    pids: set[int] = set()
    for role in raw_roles:
        if not isinstance(role, dict) or set(role) != {"id", "pid"}:
            raise Refusal("process registration role has missing or unknown fields")
        pid = _positive_integer(role.get("pid"), f"process role {role.get('id')!r} pid")
        if pid in pids:
            raise Refusal("process registration cannot assign one PID to multiple roles")
        pids.add(pid)
        try:
            identity = identity_reader(pid, timeout)
        except WatchFailure as error:
            raise Refusal(f"could not bind process role {role['id']!r}: {error}") from error
        if identity.get("pid") != pid or identity.get("pgid") != pgid:
            raise Refusal(f"process role {role['id']!r} is outside the explicit process group")
        bound_roles.append({"id": role["id"], **identity})
    return {
        "schema": BINDING_SCHEMA,
        "process_group_id": pgid,
        "roles": bound_roles,
    }


def validate_binding(spec: dict[str, Any], value: object) -> dict[str, Any]:
    validate_watch_spec(spec)
    if not isinstance(value, dict) or set(value) != {
        "schema", "process_group_id", "roles",
    }:
        raise Refusal("process binding has missing or unknown fields")
    if value.get("schema") != BINDING_SCHEMA:
        raise Refusal("process binding has an unsupported schema")
    pgid = _positive_integer(value.get("process_group_id"), "process binding group")
    if pgid == os.getpgrp():
        raise Refusal("process binding cannot name Field Kit's own process group")
    roles = value.get("roles")
    expected_ids = [role["id"] for role in spec["roles"]]
    if not isinstance(roles, list) or [
        role.get("id") for role in roles if isinstance(role, dict)
    ] != expected_ids:
        raise Refusal("process binding roles differ from the watch declaration")
    pids: set[int] = set()
    for role in roles:
        if not isinstance(role, dict) or set(role) != {
            "id", "pid", "pgid", "ps_lstart",
        }:
            raise Refusal("process binding role has missing or unknown fields")
        pid = _positive_integer(role.get("pid"), f"process binding role {role.get('id')!r} pid")
        if pid in pids or role.get("pgid") != pgid:
            raise Refusal("process binding roles must be unique members of the explicit group")
        pids.add(pid)
        if not isinstance(role.get("ps_lstart"), str) or not role["ps_lstart"].strip():
            raise Refusal("process binding role has no start-time identity")
    return value


def parse_footprint(text: str) -> tuple[int, int]:
    values: list[int] = []
    for name in ("phys_footprint", "phys_footprint_peak"):
        match = re.search(rf"(?m)^\s*{name}:\s*([0-9][0-9,]*)\s*B\b", text)
        if not match:
            raise WatchFailure(f"footprint output lacks {name}")
        values.append(int(match.group(1).replace(",", "")))
    return values[0], values[1]


def parse_rss_bytes(text: str) -> int:
    match = re.fullmatch(r"\s*([0-9]+)\s*", text)
    if not match:
        raise WatchFailure("could not parse exact-PID RSS")
    return int(match.group(1)) * 1024


def parse_swap_used_bytes(text: str) -> int:
    match = re.search(r"\bused\s*=\s*([0-9]+)(?:\.([0-9]+))?M\b", text)
    if not match:
        raise WatchFailure("could not parse vm.swapusage")
    whole = int(match.group(1)) * MIB
    fraction = match.group(2) or ""
    if not fraction:
        return whole
    numerator = int(fraction) * MIB
    denominator = 10 ** len(fraction)
    return whole + (numerator + denominator - 1) // denominator


def parse_thermal(text: str) -> tuple[bool, int]:
    normalized = " ".join(text.lower().replace("_", " ").split())
    warning = "thermal warning" in normalized and "no thermal warning" not in normalized
    speed = re.search(r"cpu speed limit\s*(?::|=)\s*([0-9]+)", normalized)
    no_power_status = "no cpu power status has been recorded" in normalized
    if "thermal warning" not in normalized or (not speed and not no_power_status):
        raise WatchFailure("could not parse thermal warning and CPU speed limit")
    return warning, 0 if speed is None else int(speed.group(1))


def sample_macos(
    binding: dict[str, Any],
    timeout_seconds: float,
    command: CommandReader = run_text,
) -> dict[str, Any]:
    roles: dict[str, dict[str, Any]] = {}
    for bound in binding["roles"]:
        observed = read_process_identity(bound["pid"], timeout_seconds, command)
        expected = {key: bound[key] for key in ("pid", "pgid", "ps_lstart")}
        if observed != expected:
            raise WatchFailure(f"process identity changed for role {bound['id']!r}")
        current, peak = parse_footprint(command(
            [FOOTPRINT, "-p", str(bound["pid"]), "-f", "bytes", "--noCategories"],
            timeout_seconds,
        ))
        rss = parse_rss_bytes(command(
            [PS, "-o", "rss=", "-p", str(bound["pid"])],
            timeout_seconds,
        ))
        roles[bound["id"]] = {
            "identity": expected,
            "current_footprint_bytes": current,
            "peak_footprint_bytes": peak,
            "rss_bytes": rss,
        }
    swap = parse_swap_used_bytes(command([SYSCTL, "-n", "vm.swapusage"], timeout_seconds))
    thermal_warning, cpu_speed_limit = parse_thermal(
        command([PMSET, "-g", "therm"], timeout_seconds)
    )
    return {
        "roles": roles,
        "swap_used_bytes": swap,
        "thermal_warning": thermal_warning,
        "cpu_speed_limit": cpu_speed_limit,
    }


def evaluate_snapshot(
    spec: dict[str, Any],
    snapshot: object,
    *,
    baseline_swap_bytes: int,
    observed_rss_peaks: dict[str, int],
    gap_milliseconds: int,
    sleep_wake_milliseconds: int = 0,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Evaluate independent role limits. RSS values are deliberately never summed."""
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "roles", "swap_used_bytes", "thermal_warning", "cpu_speed_limit",
    }:
        raise WatchFailure("process snapshot has missing or unknown fields")
    roles = snapshot.get("roles")
    expected_ids = [role["id"] for role in spec["roles"]]
    if not isinstance(roles, dict) or sorted(roles) != expected_ids:
        raise WatchFailure("process snapshot roles differ from the watch declaration")
    reasons: list[dict[str, Any]] = []
    peaks = dict(observed_rss_peaks)
    limits = {role["id"]: role for role in spec["roles"]}
    for role_id in expected_ids:
        record = roles[role_id]
        if not isinstance(record, dict) or set(record) != {
            "identity", "current_footprint_bytes", "peak_footprint_bytes", "rss_bytes",
        }:
            raise WatchFailure(f"process snapshot for role {role_id!r} is invalid")
        values = {
            name: record.get(name)
            for name in ("current_footprint_bytes", "peak_footprint_bytes", "rss_bytes")
        }
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in values.values()):
            raise WatchFailure(f"process snapshot for role {role_id!r} has an invalid counter")
        peaks[role_id] = max(peaks.get(role_id, 0), values["rss_bytes"])
        checks = (
            ("current-footprint", values["current_footprint_bytes"], limits[role_id]["current_footprint_bytes_max"]),
            ("peak-footprint", values["peak_footprint_bytes"], limits[role_id]["peak_footprint_bytes_max"]),
            ("rss", max(values["rss_bytes"], peaks[role_id]), limits[role_id]["rss_bytes_max"]),
        )
        for kind, observed, limit in checks:
            if limit is not None and observed >= limit:
                reasons.append({
                    "kind": kind,
                    "role": role_id,
                    "observed_bytes": observed,
                    "limit_bytes": limit,
                })
    swap = snapshot.get("swap_used_bytes")
    if isinstance(swap, bool) or not isinstance(swap, int) or swap < 0:
        raise WatchFailure("process snapshot has an invalid swap counter")
    swap_growth = max(0, swap - baseline_swap_bytes)
    swap_limit = spec["swap_growth_bytes_max"]
    if swap_limit is not None and swap_growth >= swap_limit:
        reasons.append({
            "kind": "swap-growth",
            "observed_bytes": swap_growth,
            "limit_bytes": swap_limit,
        })
    if not isinstance(snapshot.get("thermal_warning"), bool):
        raise WatchFailure("process snapshot has an invalid thermal state")
    speed = snapshot.get("cpu_speed_limit")
    if isinstance(speed, bool) or not isinstance(speed, int) or speed < 0:
        raise WatchFailure("process snapshot has an invalid CPU speed limit")
    if spec["thermal_stop"] and snapshot["thermal_warning"]:
        reasons.append({"kind": "thermal-warning"})
    if spec["cpu_speed_limit_stop"] and speed != 0:
        reasons.append({"kind": "cpu-speed-limit", "observed": speed})
    if gap_milliseconds > spec["max_gap_milliseconds"]:
        reasons.append({
            "kind": "watcher-gap",
            "observed_milliseconds": gap_milliseconds,
            "limit_milliseconds": spec["max_gap_milliseconds"],
        })
    if sleep_wake_milliseconds > spec["max_gap_milliseconds"]:
        reasons.append({
            "kind": "sleep-wake",
            "observed_milliseconds": sleep_wake_milliseconds,
            "limit_milliseconds": spec["max_gap_milliseconds"],
        })
    return peaks, reasons


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_bound_group(
    binding: dict[str, Any],
    grace_seconds: float,
    *,
    identity_reader: IdentityReader = read_process_identity,
    pid_exists: Callable[[int], bool] = _pid_exists,
    group_exists: Callable[[int], bool] = _group_exists,
    kill_group: Callable[[int, int], None] = os.killpg,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    command_timeout_seconds: float = 15,
) -> dict[str, Any]:
    """TERM, then reverify before KILL, only the frozen explicit group."""
    pgid = binding["process_group_id"]

    def live_verified() -> tuple[list[str], list[dict[str, Any]]]:
        live: list[str] = []
        failures: list[dict[str, Any]] = []
        for bound in binding["roles"]:
            pid = bound["pid"]
            if not pid_exists(pid):
                continue
            try:
                observed = identity_reader(pid, command_timeout_seconds)
            except WatchFailure as error:
                failures.append({"role": bound["id"], "error": str(error)})
                continue
            expected = {key: bound[key] for key in ("pid", "pgid", "ps_lstart")}
            if observed != expected or observed.get("pgid") != pgid:
                failures.append({
                    "role": bound["id"],
                    "expected": expected,
                    "observed": observed,
                })
            else:
                live.append(bound["id"])
        return live, failures

    live, failures = live_verified()
    if failures:
        return {"status": "refused-identity", "signal": None, "failures": failures}
    if not live:
        return {"status": "already-exited", "signal": None, "verified_roles": []}
    try:
        kill_group(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return {"status": "already-exited", "signal": "SIGTERM", "verified_roles": live}
    except PermissionError as error:
        return {"status": "permission-denied", "signal": "SIGTERM", "detail": str(error)}
    deadline = monotonic() + grace_seconds
    while group_exists(pgid) and monotonic() < deadline:
        sleep(min(0.1, max(0.0, deadline - monotonic())))
    if not group_exists(pgid):
        return {"status": "terminated", "signal": "SIGTERM", "verified_roles": live}
    live_after_term, failures = live_verified()
    if failures or not live_after_term:
        return {
            "status": "kill-refused-identity",
            "signal": "SIGTERM",
            "verified_roles": live_after_term,
            "failures": failures,
        }
    try:
        kill_group(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return {"status": "terminated", "signal": "SIGTERM", "verified_roles": live_after_term}
    except PermissionError as error:
        return {"status": "permission-denied", "signal": "SIGKILL", "detail": str(error)}
    return {"status": "killed", "signal": "SIGKILL", "verified_roles": live_after_term}


def append_event(path: Path, document: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise WatchFailure("process watch evidence path is unsafe")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


class CoordinatedWatcher:
    """Run the shared watcher loop; callers may place it in a dedicated thread."""

    def __init__(
        self,
        spec: dict[str, Any],
        binding: dict[str, Any],
        output: Path,
        *,
        baseline_swap_bytes: int | None = None,
        sampler: Callable[[dict[str, Any], float], dict[str, Any]] = sample_macos,
        terminator: Callable[..., dict[str, Any]] = terminate_bound_group,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.spec = validate_watch_spec(spec)
        self.binding = validate_binding(self.spec, binding)
        self.output = output
        if baseline_swap_bytes is not None and (
            isinstance(baseline_swap_bytes, bool)
            or not isinstance(baseline_swap_bytes, int)
            or baseline_swap_bytes < 0
        ):
            raise Refusal("process watch baseline swap bytes must be a non-negative integer")
        self.baseline_swap_bytes = baseline_swap_bytes
        self.sampler = sampler
        self.terminator = terminator
        self.monotonic_ns = monotonic_ns
        self.wall_ns = wall_ns

    def run(self, stop: Event) -> dict[str, Any]:
        interval = self.spec["interval_milliseconds"] / 1000
        timeout = float(self.spec["command_timeout_seconds"])
        samples = 0
        previous_ns: int | None = None
        previous_wall_ns: int | None = None
        baseline_swap = self.baseline_swap_bytes
        peaks: dict[str, int] = {}
        append_event(self.output, {
            "schema": EVENT_SCHEMA,
            "event": "watch-started",
            "binding": self.binding,
            "spec": self.spec,
        })
        while not stop.is_set():
            sampled_ns = self.monotonic_ns()
            sampled_wall_ns = self.wall_ns()
            monotonic_gap = (
                0 if previous_ns is None else max(0, (sampled_ns - previous_ns) // 1_000_000)
            )
            wall_gap = (
                0
                if previous_wall_ns is None
                else max(0, (sampled_wall_ns - previous_wall_ns) // 1_000_000)
            )
            gap = max(monotonic_gap, wall_gap)
            sleep_wake = max(0, wall_gap - monotonic_gap)
            previous_ns = sampled_ns
            previous_wall_ns = sampled_wall_ns
            try:
                snapshot = self.sampler(self.binding, timeout)
                if baseline_swap is None:
                    baseline_swap = snapshot["swap_used_bytes"]
                peaks, reasons = evaluate_snapshot(
                    self.spec,
                    snapshot,
                    baseline_swap_bytes=baseline_swap,
                    observed_rss_peaks=peaks,
                    gap_milliseconds=gap,
                    sleep_wake_milliseconds=sleep_wake,
                )
            except (KeyError, WatchFailure) as error:
                snapshot = None
                reasons = [{"kind": "required-counter-or-identity", "error": str(error)}]
            samples += 1
            record = {
                "schema": EVENT_SCHEMA,
                "event": "sample",
                "sample": samples,
                "monotonic_ns": sampled_ns,
                "wall_time_ns": sampled_wall_ns,
                "gap_milliseconds": gap,
                "sleep_wake_milliseconds": sleep_wake,
                "snapshot": snapshot,
                "observed_rss_peak_bytes": peaks,
                "stop_reasons": reasons,
            }
            if reasons:
                record["termination"] = self.terminator(
                    self.binding,
                    float(self.spec["term_grace_seconds"]),
                    command_timeout_seconds=timeout,
                )
            append_event(self.output, record)
            if reasons:
                return {
                    "state": "stopped",
                    "samples": samples,
                    "stop_reasons": reasons,
                    "termination": record["termination"],
                    "observed_rss_peak_bytes": peaks,
                }
            if stop.wait(interval):
                break
        result = {
            "state": "complete",
            "samples": samples,
            "stop_reasons": [],
            "termination": None,
            "observed_rss_peak_bytes": peaks,
        }
        append_event(self.output, {
            "schema": EVENT_SCHEMA,
            "event": "watch-complete",
            **result,
        })
        return result
