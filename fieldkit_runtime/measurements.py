"""Measurements retained independently of task quality or harness completion."""
from __future__ import annotations
import subprocess
from copy import deepcopy
from typing import Any
from . import watcher
from .serving import HTTPResponseError
GIB = 1024**3
MIB = 1024**2

POLICY = {
    "process_watch": {"interval_milliseconds": 5000, "max_gap_milliseconds": 15000,
        "command_timeout_seconds": 15, "engine_memory_fraction": 0.875, "wired_memory_fraction": 0.875,
        "helper_bytes_max": 2 * GIB, "swap_growth_bytes_max": 512 * MIB,
        "thermal_stop": True, "cpu_speed_limit_stop": True},
}

def engine_budget(facts, fraction):
    return min(int(facts["physical_memory_bytes"] * fraction), facts["wired_limit_mib"] * MIB)

def policy_for(adapters):
    policy = deepcopy(POLICY)
    policy["process_watch"]["helper_limits_by_adapter"] = {
        adapter.identifier: dict(adapter.helper_limits) for adapter in adapters}
    return policy


def watch_spec(protocol: dict[str, Any], facts: dict[str, Any], adapter) -> dict[str, Any]:
    limits = protocol["process_watch"]
    engine = int(facts["physical_memory_bytes"] * limits["engine_memory_fraction"])
    helper = limits["helper_bytes_max"]
    approved = limits.get("helper_limits_by_adapter", {}).get(adapter.identifier, {})
    roles = [{"id": "engine", "limit": engine}]
    # Existing approvals retain their original helper limit. Changed adapter
    # defaults are prospective and cannot silently enlarge an approved study.
    roles.extend({"id": role, "limit": approved.get(role, helper)} for role in adapter.helper_limits)
    return {
        "schema": watcher.WATCH_SCHEMA,
        "interval_milliseconds": limits["interval_milliseconds"],
        "max_gap_milliseconds": limits["max_gap_milliseconds"],
        "command_timeout_seconds": limits["command_timeout_seconds"],
        "roles": sorted(({"id": role["id"], "rss_bytes_max": role["limit"], "current_footprint_bytes_max": role["limit"],
                          "peak_footprint_bytes_max": role["limit"]} for role in roles), key=lambda role: role["id"]),
        "swap_growth_bytes_max": limits["swap_growth_bytes_max"],
        "wired_growth_bytes_max": engine_budget(facts, limits["wired_memory_fraction"]),
        "thermal_stop": limits["thermal_stop"],
        "cpu_speed_limit_stop": limits["cpu_speed_limit_stop"],
    }


def classify_failure(error: BaseException) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    message = str(error).lower()
    if any(text in message for text in ("not enough free disk", "weights cache is unavailable", "must be on apfs")):
        return "storage"
    if isinstance(error, HTTPResponseError) and "context" in message:
        return "exceeds-context"
    if any(text in message for text in ("out of memory", "out-of-memory", "insufficient memory", "allocation failed")):
        return "memory-failure"
    if "safety stop" in message:
        return "stopped-by-watch"
    return "runtime-failure"


def memory_record(loaded: dict[str, Any] | None, resources: dict[str, Any], native: dict[str, Any] | None) -> dict[str, Any]:
    engine_loaded = ((loaded or {}).get("roles") or {}).get("engine") or {}
    engine_peak = (resources.get("roles") or {}).get("engine") or {}
    baseline, loaded_wired = resources.get("wired_baseline_bytes"), (loaded or {}).get("wired_bytes")
    record = {
        "engine_loaded_wired_bytes": None if baseline is None or loaded_wired is None else max(0, loaded_wired - baseline),
        "engine_peak_wired_bytes": resources.get("wired_growth_bytes") if resources.get("samples") else None,
        "engine_loaded_footprint_bytes": engine_loaded.get("current_footprint_bytes"),
        "engine_loaded_rss_bytes": engine_loaded.get("rss_bytes"),
        "engine_peak_footprint_bytes": engine_peak.get("peak_footprint_bytes_max"),
        "engine_rss_max_bytes": engine_peak.get("rss_bytes_max"),
        "swap_growth_bytes": resources.get("swap_growth_bytes"),
        "loaded_roles": (loaded or {}).get("roles"),
        "peak_roles": resources.get("roles"),
    }
    if native is not None:
        record.update(native)
    record.update(counter="system-wired-growth", units="bytes", limitations=(
        "System wired growth includes other apps; process counters may omit GPU memory. "
        "Five-second sampling can miss peaks. Counters overlap."))
    return record
