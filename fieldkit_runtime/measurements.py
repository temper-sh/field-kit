"""Measurements retained independently of task quality or harness completion."""
from __future__ import annotations
import subprocess
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

def watch_spec(protocol: dict[str, Any], facts: dict[str, Any], family: str) -> dict[str, Any]:
    limits = protocol["process_watch"]
    engine = int(facts["physical_memory_bytes"] * limits["engine_memory_fraction"])
    helper = limits["helper_bytes_max"]
    roles = [{"id": "engine", "limit": engine}, {"id": "router", "limit": helper}]
    if family in ("splash", "vllm-metal"):
        roles.append({"id": "frontend", "limit": helper})
    if family == "vllm-metal":
        roles.append({"id": "resource-tracker", "limit": helper})
    if family == "rapid-mlx":
        roles.append({"id": "crash-log", "limit": helper})
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
    if isinstance(error, HTTPResponseError) and "context" in message:
        return "exceeds-context"
    if any(text in message for text in ("out of memory", "out-of-memory", "insufficient memory", "allocation failed")):
        return "memory-failure"
    if "safety stop" in message:
        return "stopped-by-watch"
    return "runtime-failure"


def rates(response: dict[str, Any]) -> dict[str, Any]:
    """Native engine rates when the engine reports them; never estimated."""
    timings = response.get("timings") or {}

    def rate(tokens: str, milliseconds: str) -> float | None:
        count, elapsed = timings.get(tokens), timings.get(milliseconds)
        if type(count) is int and count > 0 and type(elapsed) in (int, float) and elapsed > 0:
            return count * 1000 / elapsed
        return None

    usage = response.get("usage") or {}
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "prefill_tokens_per_second": rate("prompt_n", "prompt_ms"),
        "generation_tokens_per_second": rate("predicted_n", "predicted_ms"),
    }


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
        record["splash_native"] = native
    return record
