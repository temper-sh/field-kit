"""The attempt record surface: shapes, states and file names in one place.

Records stay plain JSON. Their field names are a stable surface for Workshop
and returned studies, so new fields are added here rather than ad hoc.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .common import canonical_json, load_json
from .measurements import classify_failure
from .probe import STATUS_FILE
from .process import atomic_write

STUDY_SCHEMA = "field-kit-study/v3"
TERMINAL = {"complete", "failed", "interrupted", "retained"}
RUN_LIMIT = 64 * 1024**2

def failure(error: BaseException) -> dict[str, str]:
    return {"kind": classify_failure(error), "message": str(error)}

def outcome(run: dict[str, Any]) -> str:
    if not run["safe_to_cleanup"]:
        return "retained"
    error = run.get("error")
    return "interrupted" if error and error.get("kind") == "interrupted" else "failed" if error else "complete"

def new_run(*, run_id, study, candidate, reason, created_epoch, created_at, temper, field_kit, machine,
            resource_policy, family, execution, engine_adapter, model_adapter, model_adapter_scope,
            installation, weights_cache, lock_sha256, workload_sha256) -> dict[str, Any]:
    """A reserved attempt. Every later phase updates this one record."""
    return {"id": run_id, "study": study, "candidate": candidate, "state": "queued", "reason": reason,
            "created_epoch": created_epoch, "created_at": created_at, "worker": None,
            "temper": temper, "field_kit": field_kit, "machine": machine, "resource_policy": resource_policy,
            "family": family, "execution": execution, "engine_adapter": engine_adapter,
            "model_adapter": model_adapter, "model_adapter_scope": model_adapter_scope,
            "installation": installation, "weights_cache": weights_cache,
            "lock_sha256": lock_sha256, "workload_sha256": workload_sha256,
            "safe_to_cleanup": True, "sessions": [], "error": None}

def session_row(session_id: str) -> dict[str, Any]:
    return {"id": session_id, "steps": [], "failure": None, "safe_to_cleanup": False}

def step_row(step_id: str) -> dict[str, Any]:
    return {"id": step_id, "failure": None}

def write(path: Path, document: Any) -> None:
    atomic_write(path, canonical_json(document))

class StepFiles:
    def __init__(self, folder: Path):
        self.folder = folder
        self.request, self.raw, self.response = folder / "request.json", folder / "response.sse", folder / "response.json"

class SessionFiles:
    """One Temper serve: Field Kit's intent, Temper's status proof and the driver's outputs."""
    def __init__(self, stage: Path):
        self.stage = stage
        self.intent, self.observations = stage / "intent.json", stage / "observations.json"
        self.process = stage / "process"
        self.status = self.process / STATUS_FILE
        self.command, self.stdout, self.stderr = stage / "command.json", stage / "stdout.txt", stage / "stderr.txt"
        self.output = stage / "output"

    def step(self, step_id: str) -> StepFiles:
        return StepFiles(self.stage / step_id)

class AttemptFiles:
    """The attempt directory. Status, recovery and export read the same names."""
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.run, self.result, self.failure = (self.directory / name for name in ("run.json", "result.json", "failure.md"))
        self.lock, self.workload = self.directory / "execution.lock.json", self.directory / "workload.json"
        self.prepare_command, self.worker_log = self.directory / "prepare-command.json", self.directory / "worker.log"

    def session(self, session_id: str) -> SessionFiles:
        return SessionFiles(self.directory / "sessions" / session_id)

    def sessions(self) -> list[SessionFiles]:
        return [SessionFiles(path) for path in sorted((self.directory / "sessions").glob("*")) if path.is_dir()]

    def load_run(self) -> dict[str, Any]:
        return load_json(self.run, RUN_LIMIT)

    def save_run(self, run: dict[str, Any]) -> None:
        write(self.run, run)

    def save_result(self, run: dict[str, Any]) -> None:
        """The committed outcome. A failure note invites diagnosis; it never hides the record."""
        self.save_run(run)
        write(self.result, run)
        if run.get("error"):
            import json
            atomic_write(self.failure, ("# Trial failure\n\nRun: " + run["id"] + "\n\n" + json.dumps(run["error"], indent=2) +
                "\n\nDiagnosis and repair: pending agent review. Attach the repair with field-kit repair.\n").encode())
