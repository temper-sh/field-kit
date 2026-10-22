"""Planned attempts, one at a time, without agent turns between them.

The whole plan is validated and its inputs are frozen before the first attempt:
every attempt runs from its frozen copy, so an edit during the queue cannot
change a matched comparison. The queue stops before the next attempt when a
STOP file exists in the study, and after any attempt that did not complete: it
failed, was interrupted, was stopped by the resource watch or could not prove
its shutdown. A harness's own exit status is evidence, not a stop. Files a
harness reads for itself are its own to identify.
"""
from __future__ import annotations
import datetime
import json
from pathlib import Path
import shutil
from . import jobs, lockview, records, subjects, workloads
from .adapters import interfaces
from .common import Refusal, canonical_json, digest, load_json, read_regular
from .material import validate_variant
from .process import atomic_write

FIELDS = {"candidate", "workload", "reason"}
OPTIONAL = {"lock", "engine", "model_adapter"}

def load(study, path, frozen):
    """Validate every attempt against its candidate and freeze its inputs in `frozen`."""
    path = Path(path)
    raw = read_regular(path, 4 * 1024**2)
    plan = load_json(path, 4 * 1024**2)
    entries = plan.get("attempts") if isinstance(plan, dict) else None
    if not isinstance(entries, list) or not entries:
        raise Refusal("a queue plan lists attempts: candidate, workload and reason, optionally lock, engine, model_adapter")
    state = study.load()
    approved = {row["id"]: row for row in state["candidates"]}
    ceiling = state["budgets"]["context_tokens"]
    frozen.mkdir(parents=True)
    attempts = []
    for number, entry in enumerate(entries, 1):
        def refuse(message):
            raise Refusal(f"queue attempt {number}: {message}")
        if not isinstance(entry, dict) or not FIELDS <= set(entry) or set(entry) - FIELDS - OPTIONAL:
            refuse("use " + ", ".join(sorted(FIELDS)) + "; optional " + ", ".join(sorted(OPTIONAL)))
        base = approved.get(entry["candidate"])
        if base is None:
            refuse(f"candidate {entry['candidate']!r} is outside approved material")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            refuse("name the question this attempt answers")
        local = base.get("subject") == "local"
        if ("lock" in entry and local) or ("engine" in entry and not local):
            refuse("a variant must be the same subject kind as its candidate: lock for Temper, engine for local")
        sources = {}
        for key in ("workload", "lock", "engine"):
            if key in entry:
                sources[key] = (path.parent / entry[key]).resolve()
                if not sources[key].is_file():
                    refuse(f"{key} not found: {sources[key]}")
        try:
            interface = interfaces.resolve(json.loads(base["spec"])["interface"] if local
                                           else lockview.interface(json.loads(base["lock"])))
            work = workloads.load(sources["workload"], model_adapter=entry.get("model_adapter"), interface=interface)
            if "lock" in entry:
                variant = read_regular(sources["lock"], 16 * 1024**2)
                validate_variant(json.loads(base["lock"]), json.loads(variant), ceiling)
            if "engine" in entry:
                subjects.validate_local_variant(base, subjects.local_candidate(sources["engine"], known=base["files"]), ceiling)
        except Refusal as error:
            refuse(str(error))
        resolved = {**entry, "workload": frozen / f"{number:03d}-workload.json"}
        atomic_write(resolved["workload"], canonical_json(work))  # resolved form: cwd is already absolute
        for key in ("lock", "engine"):
            if key in entry:
                resolved[key] = frozen / f"{number:03d}-{key}.json"
                atomic_write(resolved[key], read_regular(sources[key], 16 * 1024**2))
        resolved["inputs_sha256"] = {key: digest(read_regular(resolved[key], 64 * 1024**2)) for key in ("workload", "lock", "engine")
                                     if key in resolved}
        attempts.append(resolved)
    return attempts, digest(raw)

def needs_review(result):
    """Anything but complete: a failure, a resource stop (failed) or unproven shutdown (retained)."""
    if result["state"] == "complete":
        return None
    error = result.get("error") or {}
    return f"attempt ended {result['state']}" + (f" ({error['kind']}: {str(error.get('message'))[:160]})"
                                                 if error.get("kind") else "")

def run(study, plan_path, execute):
    name = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = study.directory / "queues" / name
    try:
        attempts, plan_sha256 = load(study, plan_path, folder / "inputs")
    except Refusal:
        shutil.rmtree(folder, ignore_errors=True)  # nothing ran; leave no half-frozen queue
        raise
    record_path = folder / "queue.json"
    record = {"plan": str(Path(plan_path).absolute()), "plan_sha256": plan_sha256, "started_at": jobs.now(),
              "frozen_inputs": [entry["inputs_sha256"] for entry in attempts],
              "attempts": [], "stopped": None, "not_started": len(attempts)}
    records.write(record_path, record)
    for number, entry in enumerate(attempts, 1):
        if (study.directory / "STOP").exists():
            record["stopped"] = "STOP file present; remove it to allow another queue"
            break
        try:
            directory = study.reserve(entry["candidate"], entry["workload"], entry["reason"], entry.get("lock"),
                                      model_adapter=entry.get("model_adapter"), engine_path=entry.get("engine"))
        except Refusal as error:
            record["stopped"] = f"attempt {number} was refused: {error}"
            break
        execute(study, directory)
        result = load_json(directory / "result.json", records.RUN_LIMIT)
        row = {"number": number, "run": directory.name, "candidate": entry["candidate"], "state": result["state"],
               "safe_to_cleanup": result.get("safe_to_cleanup"), "error": result.get("error")}
        record["attempts"].append(row)
        record["not_started"] = len(attempts) - number
        record["stopped"] = needs_review(result)
        records.write(record_path, record)
        if record["stopped"]:
            record["stopped"] = f"attempt {number} needs review: {record['stopped']}"
            break
    record["finished_at"] = jobs.now()
    records.write(record_path, record)
    return {**record, "record": str(record_path)}
