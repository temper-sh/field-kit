"""Read-only comparison of attempts: what differed, how time and memory went, and what came out.

It judges nothing. It places the recorded facts of attempts side by side, reads
decode rates across each response from the engine's own per-token timings, and
names what is missing instead of estimating it.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
from . import lockview, records
from .common import Refusal, canonical_json, digest, load_json
from .material import MISSING, arguments_map, setting

def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else key))
        return out
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        out = {}
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}

def short(value: Any) -> Any:
    text = value if isinstance(value, str) else None
    if text is not None and len(text) > 80:
        return f"{text[:60]}… ({len(text)} characters, sha256 {hashlib.sha256(text.encode()).hexdigest()[:12]})"
    return value

def differences(documents: list[Any]) -> dict[str, list[Any]]:
    """Only the paths whose values differ, one value per attempt."""
    flat = [flatten(document) for document in documents]
    keys = sorted(set().union(*flat))
    canon = lambda value: canonical_json(value) if value is not None else b"null"
    return {key: [short(row.get(key)) for row in flat] for key in keys if len({canon(row.get(key)) for row in flat}) > 1}

def stream(path: Path) -> dict[str, Any]:
    """Generated text identity and native decode checkpoints from one recorded stream."""
    text, series, final, tool_at = [], [], {}, None
    for line in path.read_bytes().splitlines():
        line = line.strip()
        if not line.startswith(b"data:") or line[5:].strip() == b"[DONE]":
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        timings = event.get("timings") if isinstance(event, dict) else None
        for choice in (event.get("choices") or []) if isinstance(event, dict) else []:
            delta = choice.get("delta") or {}
            text += [delta.get("content") or "", delta.get("reasoning_content") or delta.get("reasoning") or ""]
            calls = delta.get("tool_calls") or []
            text += [(call.get("function") or {}).get("arguments") or "" for call in calls]
            if calls and tool_at is None and isinstance(timings, dict):
                tool_at = timings.get("predicted_n")
        if isinstance(timings, dict):
            final = timings
            n, ms = timings.get("predicted_n"), timings.get("predicted_ms")
            if isinstance(n, int) and isinstance(ms, (int, float)) and n > 0 and (not series or n > series[-1][0]):
                series.append((n, ms))
    joined = "".join(text)
    facts = {"text_sha256": hashlib.sha256(joined.encode()).hexdigest()[:16] if joined else None, "text_characters": len(joined),
             "newly_read_tokens": final.get("prompt_n"), "cached_tokens": final.get("cache_n"),
             "prefill_ms": final.get("prompt_ms"), "generated_tokens": final.get("predicted_n"),
             "first_tool_call_at_token": tool_at, "decode_quarters": None, "sustained_ratio": None}
    if len(series) >= 8:
        total = series[-1][0]
        points = [(0, 0.0), *series]
        at = lambda target: next(point for point in points if point[0] >= target)
        marks = [at(total * quarter / 4) for quarter in range(5)]
        rates = [round((b[0] - a[0]) * 1000 / (b[1] - a[1]), 2) if b[1] > a[1] else None for a, b in zip(marks, marks[1:])]
        facts["decode_quarters"] = rates
        if rates[0] and rates[-1]:
            facts["sustained_ratio"] = round(rates[-1] / rates[0], 2)
    elif series:
        facts["decode_quarters"] = "missing: fewer than 8 timed tokens; ask for per-token timings"
    else:
        facts["decode_quarters"] = "missing: the stream carried no per-token timings"
    return facts

def generations(directory: Path, session: dict[str, Any]) -> list[dict[str, Any]]:
    stage = directory / "sessions" / session["id"]
    rows = []
    for step in session.get("steps", []):
        raw = stage / step["id"] / "response.sse"
        rows.append({"label": f"{session['id']}/{step['id']}", **{key: step.get(key) for key in (
            "input_tokens", "output_tokens", "observed_output_tokens", "prefill_tokens_per_second", "generation_tokens_per_second",
            "first_token_seconds", "first_answer_seconds", "elapsed_seconds", "finish_reason", "correct")},
            **(stream(raw) if raw.is_file() else {"decode_quarters": "missing: no raw stream"})})
    relayed = (session.get("requests") or {}).get("rows", [])
    if session.get("command") and not relayed:
        # Before the relay, only a harness's own records hold its streams; the bundled coding harness keeps them per turn.
        for raw in sorted((stage / "output").glob("turn-*/response.sse")):
            rows.append({"label": f"{session['id']}/{raw.parent.name}", "source": "harness record", **stream(raw)})
    for exchange in relayed:
        if not exchange["path"].split("?")[0].endswith("completions"):
            continue
        body = stage / "relay" / f"{exchange['number']:04d}" / "response.body"
        rows.append({"label": f"{session['id']}/request-{exchange['number']}", **{key: exchange.get(key) for key in (
            "input_tokens", "output_tokens", "observed_output_tokens", "prefill_tokens_per_second", "generation_tokens_per_second",
            "first_token_seconds", "elapsed_seconds", "finish_reason", "error")},
            **(stream(body) if body.is_file() else {"decode_quarters": "missing: no recorded response"})})
    return rows

def harness(directory: Path, session: dict[str, Any]) -> dict[str, Any] | None:
    if not session.get("command"):
        return None
    command = {key: session["command"].get(key) for key in ("returncode", "task_seconds")}
    path = directory / "sessions" / session["id"] / "output" / "result.json"
    if path.is_file():
        result = load_json(path, records.RUN_LIMIT)
        command["harness_state"] = result.get("state")
        command["assessments"] = [{"turn": item.get("turn"), "invalid": item.get("invalid_patch"),
                                   "all_pass": (item.get("evaluation") or {}).get("all_pass"),
                                   "behavior_pass": (item.get("evaluation") or {}).get("behavior_pass"),
                                   "review_exit": (item.get("review") or {}).get("exit_code")}
                                  for item in result.get("assessments", [])]
    return command

def subject_settings(directory: Path, run: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The settings an attempt ran with, and an identity of its fixed material."""
    if run.get("subject") == "local":
        spec = json.loads((directory / "local-engine.json").read_text())
        engine = run["local_engine"]
        material = digest(canonical_json({"software": engine["executable"]["software_sha256"],
                                          "files": {name: row["sha256"] for name, row in engine["files"].items()}}))
        return {"arguments": arguments_map(spec["arguments"]), "environment": spec.get("environment", {})}, material
    lock = load_json(directory / "execution.lock.json", 16 * 1024**2)
    selected = lockview.preset(lock)
    return ({"profile": selected["profiles"][0], "request_defaults": selected.get("request_defaults"),
             "speculation": selected.get("speculation")}, digest(canonical_json(lockview.frozen_material(lock))))

def compare(study, run_ids: list[str]) -> dict[str, Any]:
    if not run_ids:
        raise Refusal("name at least one attempt")
    state = study.load()
    attempts, settings, workloads_used, rows = [], [], [], {}
    for run_id in run_ids:
        files = study.files(run_id)
        if not files.result.is_file():
            raise Refusal(f"attempt {run_id} has not finished")
        run = load_json(files.result, records.RUN_LIMIT)
        chosen, material = subject_settings(files.directory, run)
        settings.append(chosen)
        workloads_used.append(load_json(files.workload, 64 * 1024**2))
        fixed = {}
        for name in state.get("fixed_settings", []):
            row = {"subject": run.get("subject"), "lock": (files.directory / "execution.lock.json").read_text()
                   if run.get("subject") != "local" else None,
                   "spec": (files.directory / "local-engine.json").read_text() if run.get("subject") == "local" else None}
            value = setting(row, name)
            fixed[name] = None if value is MISSING else value
        sessions = []
        for session in run.get("sessions", []):
            memory = session.get("memory") or {}
            produced = generations(files.directory, session)
            for item in produced:
                rows.setdefault(item["label"], {})[run_id] = item
            sessions.append({"id": session["id"], "startup_seconds": session.get("startup_seconds"),
                             "elapsed_seconds": session.get("elapsed_seconds"), "failure": session.get("failure"),
                             "power": session.get("power"),
                             "memory": {key: memory.get(key) for key in (
                                 "counter", "units", "engine_loaded_wired_bytes", "engine_peak_wired_bytes", "engine_loaded_rss_bytes",
                                 "engine_rss_max_bytes", "engine_peak_footprint_bytes", "swap_growth_bytes", "not_observed")},
                             "resources": {key: (session.get("resources") or {}).get(key) for key in (
                                 "samples", "max_gap_milliseconds", "stop_reasons")},
                             "harness": harness(files.directory, session), "generations": produced})
        attempts.append({"run": run_id, "candidate": run["candidate"], "subject": run.get("subject"), "state": run["state"],
                         "error": run.get("error"), "reason": run.get("reason"), "material": material,
                         "field_kit": (run.get("field_kit") or {}).get("sha256", "")[:16], "fixed_settings": fixed,
                         "sessions": sessions})
    identity = {label: ("identical" if len({(item.get("text_sha256"), item.get("text_characters")) for item in by_run.values()}) == 1
                        else "different") for label, by_run in rows.items() if len(by_run) == len(run_ids) and len(run_ids) > 1}
    texts = {}
    for label, by_run in rows.items():
        for run_id, item in by_run.items():
            if item.get("text_sha256"):
                texts.setdefault(item["text_sha256"], []).append(f"{run_id}:{label}")
    groups = [sorted(members) for members in texts.values() if len(members) > 1]
    fixed_changed = [name for name in state.get("fixed_settings", [])
                     if len({canonical_json(attempt["fixed_settings"].get(name)) for attempt in attempts}) > 1]
    return {"attempts": attempts,
            "same_material": len({attempt["material"] for attempt in attempts}) == 1,
            "setting_differences": differences(settings),
            "workload_differences": differences(workloads_used),
            "fixed_settings": state.get("fixed_settings", []), "fixed_settings_changed": fixed_changed,
            "output_identity": {"same_label": identity, "identical_text": sorted(groups)},
            "note": "Read-only. Decode quarters are native tokens per second over each quarter of the generated tokens; "
                    "a sustained ratio well below 1 means decoding slowed during the response. Missing values stay missing."}
