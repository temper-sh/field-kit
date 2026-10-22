"""One attempt of one Temper preset. Request quality never gates other measurements.

    begin    check frozen inputs, bind the worker, mark preparing
    prepare  refuse unsafe storage, let Temper prepare the exact lock
    measure  for each session: serve the preset, drive the work, record
    commit   outcome, or proven shutdown after a failure, then the result
"""
from __future__ import annotations
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any
from . import drivers, jobs, lockview, records, recovery, storage, workloads
from .adapters import engines, interfaces, models
from .common import Refusal, load_json
from .context import Context
from .process import exclusive_lock, file_digest, run_process_progress, runtime_identity
from .session import EngineSession

@dataclass
class Attempt:
    context: Context
    files: records.AttemptFiles
    run: dict[str, Any]
    state: dict[str, Any]
    lock: dict[str, Any]
    work: dict[str, Any]
    engine: Any
    model: Any
    interface: Any
    temper: Any
    root: Path
    cache: Path
    deadline: float

    def remaining(self, maximum):
        value = min(maximum, self.deadline - time.time())
        if value <= 0:
            raise TimeoutError("approved time budget exhausted")
        return value

    def limit(self, budget):
        return self.remaining(self.state["budgets"][budget])

    def save(self):
        self.files.save_run(self.run)

def interrupt(signum, frame):
    raise KeyboardInterrupt("stop requested")

def execute(study, directory):
    # One attempt at a time per checkout. flock is released when the process dies.
    machine_lock = study.repository / ".work" / "execution.lock"
    machine_lock.parent.mkdir(exist_ok=True)
    machine_lock.touch(exist_ok=True)
    with exclusive_lock(machine_lock):
        attempt = begin(study, records.AttemptFiles(directory))
        previous = signal.signal(signal.SIGTERM, interrupt)
        try:
            with awake(attempt.context):
                try:
                    prepare(attempt)
                    for session_id, driver in drivers.for_workload(attempt.work, attempt.interface, attempt.model):
                        if not measure(attempt, session_id, driver):
                            break
                    attempt.run["state"] = records.outcome(attempt.run)
                except (Exception, KeyboardInterrupt) as error:
                    fail(attempt, error)
        finally:
            signal.signal(signal.SIGTERM, previous)
            attempt.run["finished_at"] = jobs.now()
            attempt.files.save_result(attempt.run)
    return jobs.status(directory)

def begin(study, files):
    with exclusive_lock(study.directory / ".lock"):
        run = files.load_run()
        if run["state"] != "queued":
            raise Refusal("attempt already started; recover it and create a new trial")
        state = study.load()
        if file_digest(files.lock) != run["lock_sha256"] or file_digest(files.workload) != run["workload_sha256"]:
            raise Refusal("attempt inputs changed")
        lock = load_json(files.lock, 16 * 1024**2)
        interface = interfaces.resolve(lockview.interface(lock))
        engine = engines.from_lock(lock)
        work = workloads.load(files.workload, interface=interface)
        model = models.resolve(work["model_adapter"])
        if run.get("engine_adapter", engine.identifier) != engine.identifier or run.get("model_adapter", model.identifier) != model.identifier:
            raise Refusal("attempt adapters differ from its frozen inputs")
        temper = study.context.temper(Path(run["temper"]["path"]))
        if temper.identity() != run["temper"]:
            raise Refusal("Temper changed after this attempt was reserved; prepare a new attempt")
        run.update(worker=jobs.process_identity(os.getpid()), field_kit=runtime_identity(),
                   engine_adapter=engine.identifier, model_adapter=model.identifier,
                   model_adapter_scope="external-harness" if "command" in work else "request-workload",
                   state="preparing", safe_to_cleanup=False)
        files.save_run(run)
    return Attempt(study.context, files, run, state, lock, work, engine, model, interface, temper,
                   study.runtime(), Path(run["weights_cache"]),
                   min(state["deadline"], time.time() + state["budgets"]["trial_seconds"]))

@contextlib.contextmanager
def awake(context):
    process = None
    if context.keep_awake:
        process = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield
    finally:
        if process:
            process.terminate()
            process.wait(timeout=5)

def prepare(attempt):
    """Refuse before Temper downloads: a disconnected cache, a non-APFS root or too little disk."""
    run, volume = attempt.run, attempt.context.volume
    storage.require_cache(attempt.cache, attempt.state["weights_cache_explicit"])
    run["storage"] = {"root": volume(attempt.root), "weights_cache": volume(attempt.cache)}
    storage.require_apfs(run["storage"]["root"], "the study root")
    absent = sum(entry["bytes"] for entry in lockview.hf_files(attempt.lock).values()
                 if not storage.present(attempt.cache, entry))
    installed = (attempt.root / "software" / "installations" / run["installation"]).exists()
    run["disk_needs_bytes"] = storage.require_space(run["storage"]["root"], run["storage"]["weights_cache"],
                                                    absent, 0 if installed else lockview.software_bytes([attempt.lock]))
    attempt.save()
    attempt.root.mkdir(parents=True, exist_ok=True)
    def progress(argv, timeout, **kwargs):
        return run_process_progress(argv, timeout, identity_path=attempt.files.prepare_command, **kwargs)
    attempt.temper.progress_runner = progress
    # Only the hub cache moves; HF_HOME, and with it the user's token, stays.
    environment = {**os.environ, "HF_HUB_CACHE": str(attempt.cache), "HF_HUB_DISABLE_TELEMETRY": "1"}
    prepared = attempt.temper.prepare(attempt.files.lock, attempt.root, run["installation"],
                                      attempt.remaining(21600), environment)
    if prepared.get("execution") != run["execution"]:
        raise Refusal("Temper prepared a different execution")
    run["preparation"] = {"generation": prepared["generation"]}
    run.update(state="measuring", safe_to_cleanup=True)
    attempt.save()

def measure(attempt, session_id, driver):
    """One fresh serve. Returns whether the next session may run."""
    run = attempt.run
    run["safe_to_cleanup"] = False
    attempt.save()
    files = attempt.files.session(session_id)
    files.stage.mkdir(parents=True, exist_ok=False)
    row = records.session_row(session_id)
    engine = EngineSession(attempt, files)
    try:
        with engine:
            row["startup_seconds"] = engine.startup_seconds
            driver.run(engine, files, row, attempt)
    except (Exception, KeyboardInterrupt) as error:
        row["failure"] = records.failure(error)
    row.update(engine.finish())
    records.write(files.observations, row)
    run["sessions"].append(row)
    run["safe_to_cleanup"] = row["safe_to_cleanup"]
    attempt.save()
    if row["failure"] or not row["safe_to_cleanup"] or row["resources"].get("stop_reasons"):
        run["error"] = row["failure"] or {"kind": "resource-stop", "message": "inspect session resources"}
        return False
    return True

def fail(attempt, error):
    """Keep the attempt; mark it safe to clean only with proof that nothing still runs."""
    attempt.run["error"] = records.failure(error)
    try:
        recovery.require_shutdown(attempt.files)
        attempt.run["safe_to_cleanup"] = True
    except Refusal:
        attempt.run["safe_to_cleanup"] = False
    attempt.run["state"] = records.outcome(attempt.run)
