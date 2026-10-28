"""Planned attempts, one at a time, without agent turns between them.

The whole plan is validated and its inputs are frozen before the first attempt:
every attempt runs from its frozen copy, so an edit during the queue cannot
change a matched comparison. The queue stops before the next attempt when a
STOP file exists in the study, and after any attempt that did not complete: it
failed, was interrupted, was stopped by the resource watch or could not prove
its shutdown. A harness's own exit status is evidence, not a stop. Files a
harness reads for itself are its own to identify.

Each attempt's run record names its queue entry at reservation, so a stopped or
crashed queue resumes from the attempts' own records and its frozen inputs.
Entries never started and entries whose last attempt was interrupted run as new
attempts; a complete attempt never repeats; a failed or retained attempt stands
as evidence unless the resume names it for a retry.
"""
from __future__ import annotations
import contextlib
import datetime
import json
from pathlib import Path
import shutil
from . import jobs, lockview, records, subjects, workloads
from .adapters import interfaces
from .common import Refusal, canonical_json, digest, load_json, read_regular
from .material import validate_variant
from .process import atomic_write, exclusive_lock

FIELDS = {"candidate", "workload", "reason"}
OPTIONAL = {"lock", "engine", "model_adapter"}
FROZEN = ("workload", "lock", "engine")

def input_path(inputs, number, key):
    return inputs / f"{number:03d}-{key}.json"

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
        for key in FROZEN:
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
        resolved = {**entry, "workload": input_path(frozen, number, "workload")}
        atomic_write(resolved["workload"], canonical_json(work))  # resolved form: cwd is already absolute
        for key in ("lock", "engine"):
            if key in entry:
                resolved[key] = input_path(frozen, number, key)
                atomic_write(resolved[key], read_regular(sources[key], 16 * 1024**2))
        resolved["inputs_sha256"] = {key: digest(read_regular(resolved[key], 64 * 1024**2)) for key in FROZEN
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

@contextlib.contextmanager
def owned(folder):
    """Held by the process driving this queue; the kernel releases it when that process dies."""
    lock = folder / ".lock"
    lock.touch(exist_ok=True)
    with contextlib.ExitStack() as held:
        try:
            held.enter_context(exclusive_lock(lock))
        except Refusal as error:
            if not isinstance(error.__cause__, BlockingIOError):
                raise
            raise Refusal("this queue is still running; wait for it, or create STOP in the study to end it "
                          "after the current attempt") from error
        yield

def run(study, plan_path, execute):
    name = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = study.directory / "queues" / name
    folder.mkdir(parents=True)
    with owned(folder):
        try:
            attempts, plan_sha256 = load(study, plan_path, folder / "inputs")
        except Refusal:
            shutil.rmtree(folder, ignore_errors=True)  # nothing ran; leave no half-frozen queue
            raise
        record = {"plan": str(Path(plan_path).absolute()), "plan_sha256": plan_sha256, "started_at": jobs.now(),
                  "frozen_inputs": [entry["inputs_sha256"] for entry in attempts],
                  "entries": [{key: entry[key] for key in ("candidate", "reason", "model_adapter") if key in entry}
                              for entry in attempts],
                  "attempts": [], "stopped": None, "not_started": len(attempts), "resumed_at": []}
        records.write(folder / "queue.json", record)
        return drive(study, folder, record, attempts, range(1, len(attempts) + 1), execute)

def resume(study, name, execute, retry=()):
    """Continue a stopped or crashed queue from its frozen inputs. Earlier attempts stay; a rerun is a new attempt."""
    if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
        raise Refusal("name a queue folder from the study's queues/")
    folder = study.directory / "queues" / name
    if folder.is_symlink() or not (folder / "queue.json").is_file():
        raise Refusal(f"no queue record at {folder}; that queue never started an attempt")
    with owned(folder):
        record = load_json(folder / "queue.json", records.RUN_LIMIT)
        if "entries" not in record:
            raise Refusal("this queue predates resume: its attempts do not name their entries; start a new queue")
        attempts = frozen(folder, record)
        study.require_idle()
        record["attempts"] = reconcile(study, name)
        latest = {row["number"]: row for row in record["attempts"]}
        for number in retry:
            if not 1 <= number <= len(attempts):
                raise Refusal(f"queue entry {number} does not exist; entries are 1 to {len(attempts)}")
            if latest.get(number, {}).get("state") == "complete":
                raise Refusal(f"queue entry {number} completed as {latest[number]['run']}; "
                              "repeating finished work needs a new queue")
        numbers = pending(record, retry)
        if not numbers:
            raise Refusal("every entry has a finished attempt; name a failed entry with --retry after review, "
                          "or start a new queue")
        record.update(stopped=None, not_started=not_started(record))
        record.pop("finished_at", None)
        record["resumed_at"].append(jobs.now())
        records.write(folder / "queue.json", record)
        return drive(study, folder, record, attempts, numbers, execute)

def drive(study, folder, record, attempts, numbers, execute):
    """Run the numbered entries in order until a STOP file, a refusal or an attempt that needs review."""
    path = folder / "queue.json"
    for number in numbers:
        entry = attempts[number - 1]
        if (study.directory / "STOP").exists():
            record["stopped"] = "STOP file present; remove it to allow another queue"
            break
        try:
            directory = study.reserve(entry["candidate"], entry["workload"], entry["reason"], entry.get("lock"),
                                      model_adapter=entry.get("model_adapter"), engine_path=entry.get("engine"),
                                      queue={"id": folder.name, "entry": number})
        except Refusal as error:
            record["stopped"] = f"attempt {number} was refused: {error}"
            break
        execute(study, directory)
        result = load_json(directory / "result.json", records.RUN_LIMIT)
        record["attempts"].append(row(number, directory.name, result))
        record["not_started"] = not_started(record)
        record["stopped"] = needs_review(result)
        records.write(path, record)
        if record["stopped"]:
            record["stopped"] = f"attempt {number} needs review: {record['stopped']}"
            break
    record["finished_at"] = jobs.now()
    records.write(path, record)
    returned = {**record, "record": str(path)}
    if pending(record):
        returned["resume"] = f"./field-kit study queue {study.name} --resume {folder.name}"
    return returned

def row(number, run, result):
    return {"number": number, "run": run, "candidate": result["candidate"], "state": result["state"],
            "safe_to_cleanup": result.get("safe_to_cleanup"), "error": result.get("error")}

def not_started(record):
    return len(record["entries"]) - len({row["number"] for row in record["attempts"]})

def pending(record, retry=()):
    """Entries a resume runs, in plan order: never started, last attempt interrupted, or named for retry."""
    latest = {row["number"]: row["state"] for row in record["attempts"]}
    return [number for number in range(1, len(record["entries"]) + 1)
            if number not in latest or latest[number] == "interrupted" or number in retry]

def frozen(folder, record):
    """The entries as frozen at the start; a changed copy would break the matched comparison."""
    attempts = []
    for number, (entry, expected) in enumerate(zip(record["entries"], record["frozen_inputs"]), 1):
        resolved = dict(entry)
        for key, sha256 in expected.items():
            resolved[key] = input_path(folder / "inputs", number, key)
            if digest(read_regular(resolved[key], 64 * 1024**2)) != sha256:
                raise Refusal(f"frozen queue input changed: {resolved[key]}; start a new queue")
        attempts.append(resolved)
    return attempts

def reconcile(study, name):
    """Rows for every attempt reserved for this queue, from the attempts' own records, in study order.

    The committed result is the outcome; a result lost in a crash falls back to run.json."""
    rows = []
    for run in study.load()["runs"]:
        files = study.files(run)
        current = files.load_run()
        link = current.get("queue") or {}
        if link.get("id") == name:
            rows.append(row(link["entry"], run, load_json(files.result, records.RUN_LIMIT) if files.result.exists() else current))
    return rows
