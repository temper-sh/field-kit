"""Planned attempts, one at a time, without agent turns between them.

The queue stops before the next attempt when a STOP file exists in the study,
and after any attempt that did not complete: it failed, was interrupted, was
stopped by the resource watch or could not prove its shutdown. A harness's own
exit status is evidence, not a stop. Every attempt is an ordinary reserved attempt with its own reason.
"""
from __future__ import annotations
import datetime
from pathlib import Path
from . import jobs, records
from .common import Refusal, digest, load_json, read_regular

FIELDS = {"candidate", "workload", "reason"}
OPTIONAL = {"lock", "engine", "model_adapter"}

def load(study, path):
    """Check the whole plan before anything runs; relative paths resolve beside the plan."""
    path = Path(path)
    raw = read_regular(path, 4 * 1024**2)
    plan = load_json(path, 4 * 1024**2)
    entries = plan.get("attempts") if isinstance(plan, dict) else None
    if not isinstance(entries, list) or not entries:
        raise Refusal("a queue plan lists attempts: candidate, workload and reason, optionally lock, engine, model_adapter")
    approved = {row["id"] for row in study.load()["candidates"]}
    attempts = []
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not FIELDS <= set(entry) or set(entry) - FIELDS - OPTIONAL:
            raise Refusal(f"queue attempt {number}: use " + ", ".join(sorted(FIELDS)) + "; optional " + ", ".join(sorted(OPTIONAL)))
        if entry["candidate"] not in approved:
            raise Refusal(f"queue attempt {number}: candidate {entry['candidate']!r} is outside approved material")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise Refusal(f"queue attempt {number}: name the question this attempt answers")
        resolved = {**entry}
        for key in ("workload", "lock", "engine"):
            if key in entry:
                resolved[key] = (path.parent / entry[key]).resolve()
                if not resolved[key].is_file():
                    raise Refusal(f"queue attempt {number}: {key} not found: {resolved[key]}")
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
    attempts, plan_sha256 = load(study, plan_path)
    name = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    record_path = study.directory / "queues" / f"{name}.json"
    record_path.parent.mkdir(exist_ok=True)
    record = {"plan": str(Path(plan_path).absolute()), "plan_sha256": plan_sha256, "started_at": jobs.now(),
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
