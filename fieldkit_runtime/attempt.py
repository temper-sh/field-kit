"""One attempt of one subject. Request quality never gates other measurements.

    begin    check frozen inputs, bind the worker, mark preparing
    prepare  the subject's own preparation: Temper prepares a lock, or the
             approved local engine is checked unchanged
    measure  for each session: serve the subject, drive the work, record
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
from . import drivers, jobs, lockview, records, recovery, subjects, workloads
from .adapters import engines, interfaces, models
from .common import Refusal, load_json
from .context import Context
from .process import exclusive_lock, file_digest, runtime_identity
from .session import EngineSession

@dataclass
class Attempt:
    context: Context
    files: records.AttemptFiles
    run: dict[str, Any]
    state: dict[str, Any]
    lock: dict[str, Any] | None
    work: dict[str, Any]
    engine: Any
    model: Any
    interface: Any
    temper: Any
    root: Path
    cache: Path | None
    deadline: float
    subject: Any = None

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
        if any(file_digest(path) != expected for path, expected in files.inputs(run)):
            raise Refusal("attempt inputs changed")
        local = run.get("subject") == "local"
        if local:
            lock, spec = None, load_json(files.engine)
            interface, engine = interfaces.resolve(spec["interface"]), engines.resolve(spec["engine_adapter"])
        else:
            lock = load_json(files.lock, 16 * 1024**2)
            interface, engine = interfaces.resolve(lockview.interface(lock)), engines.from_lock(lock)
        work = workloads.load(files.workload, interface=interface)
        model = models.resolve(work["model_adapter"]) if work["model_adapter"] else None
        model_id = model.identifier if model else None
        if run.get("engine_adapter", engine.identifier) != engine.identifier or run.get("model_adapter", model_id) != model_id:
            raise Refusal("attempt adapters differ from its frozen inputs")
        temper = study.context.temper(Path(run["temper"]["path"]))
        if not local and temper.identity() != run["temper"]:
            raise Refusal("Temper changed after this attempt was reserved; prepare a new attempt")
        run.update(worker=jobs.process_identity(os.getpid()), field_kit=runtime_identity(),
                   engine_adapter=engine.identifier, model_adapter=model_id,
                   model_adapter_scope=workloads.adapter_scope(work),
                   state="preparing", safe_to_cleanup=False)
        files.save_run(run)
    attempt = Attempt(study.context, files, run, state, lock, work, engine, model, interface, temper,
                      study.runtime(), Path(run["weights_cache"]) if run.get("weights_cache") else None,
                      min(state["deadline"], time.time() + state["budgets"]["trial_seconds"]))
    attempt.subject = subjects.for_attempt(attempt)
    return attempt

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
    attempt.subject.prepare()
    attempt.run.update(state="measuring", safe_to_cleanup=True)
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
