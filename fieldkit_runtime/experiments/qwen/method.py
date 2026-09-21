"""Frozen Qwen study decisions. Measurements remain attributable to each Mac."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import statistics


SCHEMA = "field-kit-qwen-machine-study/v2"
SELECTOR = "qwen-machine-study@2"
FIELDS = ["completed-work", "context", "fit", "interaction", "limits", "profile", "tuning"]
FLAG_VARIANTS = ("batch-1024", "mtp-off", "cache-reference", "kv-q4")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def grade(content: object, expected: object) -> dict:
    """The task asks for one JSON artifact; key order and whitespace may vary."""
    try:
        if not isinstance(content, str):
            raise ValueError("missing delivered content")
        actual = json.loads(content, object_pairs_hook=_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-JSON number")))
    except (ValueError, TypeError) as error:
        return {"correct": False, "reason": str(error)}
    # Canonical JSON also keeps bool distinct from int, unlike Python equality.
    equal = json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
    return {"correct": equal, "reason": "required artifact delivered" if equal else "artifact differs from the source-backed oracle"}


def record_value(seed: int, ordinal: int) -> str:
    """Stable source facts shared by context construction and result review."""
    return hashlib.sha256(f"field-kit-context-v1:{seed}:{ordinal}".encode()).hexdigest()[:16]


def variant_records(lock: dict, model: str, variant: str, *, cache=None, window=None) -> dict:
    """Change only the named hypothesis; Temper compiles the resulting catalog."""
    records = copy.deepcopy(lock["records"])
    layout = records["layouts"][model]
    engine = layout["engine_config"]
    if variant == "batch-1024":
        engine.update(batch_tokens=1024, microbatch_tokens=512)
    elif variant == "mtp-off":
        layout["speculation"] = {"method": "none", "source": "none", "max_draft_tokens": 0}
    elif variant == "cache-reference":
        engine.update(context_checkpoints=16, prompt_cache_ram_mib=0)
    elif variant == "kv-q4":
        engine["kv_cache"] = "q4"
    elif variant == "context":
        if cache not in ("q4", "q8") or window not in (16384, 32768, 65536, 98304, 131072):
            raise ValueError("context point lies outside the frozen search")
        layout["context_window_tokens"] = window
        engine["kv_cache"] = cache
    elif variant != "baseline":
        raise ValueError("unknown study configuration")
    return records


def finite(value, *, positive=False) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def valid_work(run: dict | None) -> bool:
    return bool(run and run.get("valid") is True and run.get("correct") is True
                and run.get("cases") and all(row.get("correct") is True
                and finite(row.get("service_seconds"), positive=True) for row in run["cases"]))


def performance_summary(run: dict) -> dict:
    rows = run.get("cases", [])
    def median(key):
        values = [row[key] for row in rows if finite(row.get(key), positive=True)]
        return statistics.median(values) if values else None
    return {
        "configuration": run["configuration"], "valid": run.get("valid", False),
        "prefill_tokens_per_second_median": median("prefill_tokens_per_second"),
        "generation_tokens_per_second_median": median("generation_tokens_per_second"),
        "total_service_seconds": sum(row["service_seconds"] for row in rows if finite(row.get("service_seconds"))),
        "correct_cases": sum(row.get("correct") is True for row in rows), "measured_cases": len(rows),
        "peak_engine_rss_bytes": run.get("resources", {}).get("roles", {}).get("engine", {}).get("rss_bytes_max"),
        "swap_growth_bytes": run.get("resources", {}).get("swap_growth_bytes"),
        "summary_rule": "per-request medians over this frozen workload; prefill counts newly processed tokens; generation includes reasoning; total service includes first model load",
        "scenarios": [{key: row.get(key) for key in ("id", "transition", "service_seconds", "first_token_seconds", "first_answer_seconds", "usage", "prefill_tokens_per_second", "generation_tokens_per_second", "correct")} for row in rows],
    }


def choose_candidate(baseline: dict, screens: list[dict], worthwhile: float) -> str | None:
    if not valid_work(baseline):
        return None
    baseline_ids = [row["id"] for row in baseline["cases"]]
    total = sum(row["service_seconds"] for row in baseline["cases"])
    eligible = [run for run in screens if valid_work(run)
                and [row["id"] for row in run["cases"]] == baseline_ids
                and sum(row["service_seconds"] for row in run["cases"]) <= total * (1 - worthwhile / 100)]
    return min(eligible, key=lambda run: sum(row["service_seconds"] for row in run["cases"]))["configuration"] if eligible else None


def confirm_candidate(runs: list[dict], worthwhile: float, regression: float) -> dict:
    unresolved = {"selection": "unresolved", "reason": "four valid, correct, matched confirmation runs are required"}
    if len(runs) != 4 or not all(valid_work(run) for run in runs):
        return unresolved
    ids = [row["id"] for row in runs[0]["cases"]]
    candidate = runs[1]["configuration"]
    if candidate == "baseline" or len(set(ids)) != len(ids) or [run["configuration"] for run in runs] != ["baseline", candidate, candidate, "baseline"] or any([row["id"] for row in run["cases"]] != ids for run in runs):
        return unresolved
    blocks = []
    for reference, measured in ((runs[0], runs[1]), (runs[3], runs[2])):
        ref, cand = reference["cases"], measured["cases"]
        reference_total = sum(row["service_seconds"] for row in ref)
        candidate_total = sum(row["service_seconds"] for row in cand)
        passed = candidate_total * 100 <= reference_total * (100 - worthwhile) and all(
            b["service_seconds"] * 100 <= a["service_seconds"] * (100 + regression)
            for a, b in zip(ref, cand))
        blocks.append({
            "improvement_percent": 100 * (1 - candidate_total / reference_total),
            "case_regression_percent": {a["id"]: 100 * (b["service_seconds"] / a["service_seconds"] - 1) for a, b in zip(ref, cand)},
            "supports_candidate": passed,
        })
    selected = candidate if all(block["supports_candidate"] for block in blocks) else "baseline"
    return {"selection": selected, "blocks": blocks,
            "reason": "provisional choice for this machine and workload; candidate settings are a single tested change, not a combined optimum"}


def context_summary(points: list[dict], initial_seconds: float, followup_seconds: float) -> dict:
    result = {}
    for cache in ("q8", "q4"):
        rows = [row for row in points if row["cache"] == cache]
        good = [row for row in rows if row.get("valid") is True and row.get("correct") is True]
        useful = [row for row in good if finite(row.get("initial_seconds")) and finite(row.get("followup_seconds"))
                  and row["initial_seconds"] <= initial_seconds and row["followup_seconds"] <= followup_seconds]
        result[cache] = {
            "highest_witnessed_input_tokens": max((row["input_tokens"] for row in good), default=None),
            "within_budget_window_tokens": max((row["window"] for row in useful), default=None),
            "unknown_points": [row["window"] for row in rows if row.get("valid") is not True],
            "incorrect_points": [row["window"] for row in rows if row.get("valid") is True and row.get("correct") is not True],
            "latency_budget_seconds": {"initial": initial_seconds, "followup": followup_seconds},
            "boundary": "sampled points and distributed retrieval/follow-up only; no untested interval, global maximum or broad long-document quality claim",
        }
    return result
