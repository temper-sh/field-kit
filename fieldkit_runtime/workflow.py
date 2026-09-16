"""Field Kit orchestration over Temper's stable install/check primitives."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from .actions import (
    ACTION_SCHEMA,
    initial_action_request,
    propose_action,
    validate_action_candidate,
)
from .answers import validate_answers
from .artifacts import Step as ActionStep
from .artifacts import bound_material_generations, build_step, validate_step_result
from .catalog import MachineFacts, QuestionPackage, Refusal, canonical_json, digest
from .planner import PROBE_LISTEN, Plan, build_plan, load_plan, planned_paths


SESSION_SCHEMA = "field-kit-session/v2"
EXPORT_SCHEMA = "field-kit-evidence-export/v2"
EVIDENCE_DISCLOSURE = (
    "Retained evidence and exports may include generated model answers, private "
    "machine facts, local paths, hashes, timings and measurements."
)
GENERATION = re.compile(r"^[0-9a-f]{64}$")
SAFE_TOKEN = re.compile(r"^[a-z0-9]+(?:[._/+:-][a-z0-9]+)*$")


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class CommandFailure(RuntimeError):
    def __init__(self, arguments: Sequence[str], result: CommandResult):
        name = Path(arguments[0]).name if arguments else "command"
        detail = result.stderr.decode(errors="replace").strip()[-1000:]
        super().__init__(f"{name} exited {result.returncode}" + (f": {detail}" if detail else ""))
        self.result = result


def _copy_stream(source: Any, destination: Any | None, capture: bytearray) -> None:
    while True:
        chunk = source.read1(8192) if hasattr(source, "read1") else source.read(8192)
        if not chunk:
            break
        capture.extend(chunk)
        if destination is not None:
            destination.buffer.write(chunk)
            destination.flush()


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _run_process(
    arguments: Sequence[str],
    timeout_seconds: float,
    destinations: tuple[Any | None, Any | None],
    *, protocol_interrupt: bool = False,
) -> CommandResult:
    process = subprocess.Popen(
        list(arguments), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    threads = [
        threading.Thread(target=_copy_stream, args=(process.stdout, destinations[0], stdout), daemon=True),
        threading.Thread(target=_copy_stream, args=(process.stderr, destinations[1], stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    interrupted = False
    try:
        returncode = process.wait(timeout=timeout_seconds if timeout_seconds > 0 else None)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_group(process)
        returncode = 124
    except KeyboardInterrupt:
        interrupted = True
        if protocol_interrupt:
            # The protocol owns its probe and needs time to stop it, retain first
            # attempts, and commit a partial result. The normal report validator
            # still decides whether that result can be accepted.
            print("\nStopping the study and saving observations; please wait ...", flush=True)
            process.send_signal(signal.SIGTERM)
            try:
                returncode = process.wait(timeout=120)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                _stop_group(process)
                returncode = 130
        else:
            _stop_group(process)
            returncode = 130
    for thread in threads:
        thread.join()
    process.stdout.close()
    process.stderr.close()
    if timed_out:
        stderr.extend(f"Field Kit stopped the command after {timeout_seconds:.0f} seconds\n".encode())
    if interrupted:
        stderr.extend(b"Field Kit stopped the command after interruption\n")
    return CommandResult(bytes(stdout), bytes(stderr), returncode)


def run_process(arguments: Sequence[str], timeout_seconds: float = 0) -> CommandResult:
    """Run one exact argv without a shell, teeing and retaining its output."""
    return _run_process(arguments, timeout_seconds, (sys.stdout, sys.stderr))


def run_process_silent(arguments: Sequence[str], timeout_seconds: float = 0) -> CommandResult:
    """Run one exact argv while reserving stdout for a canonical CLI response."""
    return _run_process(arguments, timeout_seconds, (None, None))


def run_contributor_process(arguments: Sequence[str], timeout_seconds: float = 0) -> CommandResult:
    """Quiet installation commands, live measurement progress, graceful Ctrl-C."""
    if "--action" in arguments and "--field-kit-runtime" in arguments:
        return _run_process(arguments, timeout_seconds, (sys.stdout, sys.stderr), protocol_interrupt=True)
    return run_process_silent(arguments, timeout_seconds)


Runner = Callable[[Sequence[str], float], CommandResult]


def capture_process(arguments: Sequence[str], timeout_seconds: float = 0) -> CommandResult:
    """Run one exact read-only identity command without writing to the terminal."""
    try:
        completed = subprocess.run(
            list(arguments),
            capture_output=True,
            check=False,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            timeout=timeout_seconds if timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            error.stdout or b"",
            (error.stderr or b"") + f"Field Kit stopped the command after {timeout_seconds:.0f} seconds\n".encode(),
            124,
        )
    return CommandResult(completed.stdout, completed.stderr, completed.returncode)


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    staged = Path(staged_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        if staged.exists():
            staged.unlink()


def _write_new_or_same(path: Path, data: bytes, mode: int = 0o600) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise Refusal(f"refusing to replace different existing file: {path}")
        return
    _atomic_write(path, data, mode)


@contextmanager
def _exclusive_session_lock(path: Path):
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"session lock is not a regular file: {path}")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise Refusal(f"session lock is not a regular file: {path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Refusal("this Field Kit session is already running") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def default_paths(root: Path) -> dict[str, Path]:
    return planned_paths(root)


def _regular_tree_bytes(root: Path) -> int:
    if root.is_symlink() or not root.is_dir():
        raise Refusal(f"evidence root is absent or unsafe: {root}")
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise Refusal(f"evidence contains a symlink: {path}")
        if path.is_file():
            total += path.stat().st_size
        elif not path.is_dir():
            raise Refusal(f"evidence contains a non-regular entry: {path}")
    return total


def _retained_evidence_bytes(session: dict[str, Any]) -> int:
    total = _regular_tree_bytes(Path(session["paths"]["evidence"]))
    seen: set[Path] = set()
    for label in ("plan", "session", "report"):
        path = Path(session["paths"][label])
        if path in seen or not path.exists():
            continue
        seen.add(path)
        if path.is_symlink() or not path.is_file():
            raise Refusal(f"retained {label} evidence is unsafe: {path}")
        total += path.stat().st_size
    return total




def _version_key(value: str) -> tuple[int, int, int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-alpha\.(\d+))?", value)
    if not match:
        raise Refusal(f"unsupported Temper version format: {value}")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    alpha = match.group(4)
    return major, minor, patch, 1 if alpha is None else 0, 0 if alpha is None else int(alpha)


def _temper_identity(path: Path, minimum: str, runner: Runner) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"Temper must be an explicit regular executable: {path}")
    result = runner([str(path), "version"], 30)
    if result.returncode != 0:
        raise CommandFailure([str(path), "version"], result)
    version = result.stdout.decode(errors="replace").strip()
    if not version.startswith("temper "):
        raise Refusal("Temper returned an unexpected version identity")
    value = version.removeprefix("temper ")
    if value != "0.0.0-dev" and _version_key(value) < _version_key(minimum):
        raise Refusal(f"Field Kit requires Temper {minimum} or newer, found {value}")
    return {"path": str(path), "sha256": _file_digest(path), "version": version}


def _file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _runtime_digest() -> str:
    value = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.glob("*.py")):
        data = path.read_bytes()
        value.update(path.name.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return value.hexdigest()


def _runtime_identity() -> dict[str, str]:
    python_path = Path(sys.executable).resolve()
    if python_path.is_symlink() or not python_path.is_file():
        raise Refusal(f"Python runtime is not a regular executable: {python_path}")
    return {
        "version": __version__,
        "sha256": _runtime_digest(),
        "python_path": str(python_path),
        "python_version": platform.python_version(),
        "python_sha256": _file_digest(python_path),
    }


class Workflow:
    def __init__(
        self,
        entry: QuestionPackage,
        facts: MachineFacts,
        facts_data: bytes,
        temper: Path,
        runner: Runner = run_process,
        progress: Callable[[str], None] = print,
    ):
        self.entry = entry
        self.facts = facts
        self.facts_data = facts_data
        self.temper = temper.expanduser().resolve()
        self.runner = runner
        self.progress = progress

    def _enforce_evidence_ceiling(self, session: dict[str, Any]) -> int:
        used = _retained_evidence_bytes(session)
        maximum = self.entry.package["cost"]["evidence_bytes_max"]
        if used > maximum:
            raise Refusal(
                f"declared evidence byte ceiling was exceeded: {used} > {maximum}"
            )
        return used

    def plan(self, root_path: Path, outcome: str) -> Plan:
        identity_runner = (
            capture_process
            if self.runner in {run_process, run_process_silent}
            else self.runner
        )
        temper = _temper_identity(
            self.temper,
            self.entry.package["host"]["minimum_version"],
            identity_runner,
        )
        return build_plan(
            self.entry,
            self.facts,
            self.facts_data,
            root_path,
            outcome,
            temper,
            _runtime_identity(),
        )

    def start(self, plan: Plan, approved_plan_sha256: str) -> tuple[dict[str, Any], Path]:
        if approved_plan_sha256 != plan.sha256:
            raise Refusal("approval does not match the exact Field Kit plan")
        execution = plan.document.get("execution", {})
        plan_paths = execution.get("paths", {}) if isinstance(execution, dict) else {}
        root_value = plan_paths.get("root") if isinstance(plan_paths, dict) else None
        outcome = execution.get("outcome") if isinstance(execution, dict) else None
        if not isinstance(root_value, str) or not isinstance(outcome, str):
            raise Refusal("plan has no exact root or outcome")
        expected = self.plan(Path(root_value), outcome)
        if expected.data != plan.data or expected.sha256 != plan.sha256:
            raise Refusal("plan inputs changed before the run could start")
        plan = expected
        root = Path(root_value)
        paths = default_paths(root)
        for label, path in paths.items():
            if path.exists() or path.is_symlink():
                raise Refusal(f"{label} path already exists: {path}")
        temper = plan.document["host"]["temper"]
        runtime = plan.document["host"]["field_kit_runtime"]
        session_id = "field-kit-" + uuid.uuid4().hex
        marker_document = {
            "schema": "field-kit-owned-root/v1",
            "session_id": session_id,
            "package": self.entry.selector,
            "package_sha256": self.entry.package_sha256,
            "plan_sha256": plan.sha256,
            "root": str(root),
        }
        marker_data = canonical_json(marker_document)
        stages = [
            {
                "id": stage["id"],
                "operation": stage["operation"],
                "state": "pending",
                "attempts": [],
            }
            for stage in plan.document["execution"]["setup_stages"]
        ]
        stages.append({
            "id": "field-kit-outcome",
            "operation": "outcome",
            "state": "pending",
            "attempts": [],
            "owner": "field-kit-runtime",
        })
        created_root = False
        created_evidence = False
        created_plan = False
        created_lock = False
        try:
            root.mkdir(mode=0o700, parents=False)
            created_root = True
            paths["evidence"].mkdir(mode=0o700, parents=False)
            created_evidence = True
            _atomic_write(paths["plan"], plan.data)
            created_plan = True
            _atomic_write(paths["lock"], b"")
            created_lock = True
            package_root = root / "field-kit" / "package"
            package_root.mkdir(mode=0o700, parents=True)
            _atomic_write(package_root / "package.json", self.entry.package_data)
            for relative, data in self.entry.files.items():
                _atomic_write(package_root / relative, data)
            inputs_path = root / "field-kit" / "inputs"
            prepared = self._prepare_execution(package_root, inputs_path)
            software_lock_path = inputs_path / "software.lock.yaml"
            machine_path = root / "field-kit" / "machine-facts.yaml"
            _atomic_write(machine_path, self.facts_data)
            marker_path = root / ".field-kit-owner.json"
            _atomic_write(marker_path, marker_data)
            session: dict[str, Any] = {
                "schema": SESSION_SCHEMA,
                "id": session_id,
                "state": "running",
                "package": {
                    "selector": self.entry.selector,
                    "sha256": self.entry.package_sha256,
                    "runner_sha256": self.entry.package["mechanics"]["runner"]["sha256"],
                    "protocol": self.entry.package["mechanics"]["runtime_protocol"],
                },
                "plan": {
                    "path": str(paths["plan"]),
                    "sha256": plan.sha256,
                },
                "temper": temper,
                "field_kit_runtime": runtime,
                "machine_facts": self.facts.document,
                "outcome": outcome,
                "paths": {
                    "root": str(root),
                    "plan": str(paths["plan"]),
                    "session": str(paths["session"]),
                    "lock": str(paths["lock"]),
                    "evidence": str(paths["evidence"]),
                    "report": str(paths["report"]),
                    "package_root": str(package_root),
                    "software_lock": str(software_lock_path),
                    "machine_facts": str(machine_path),
                    "marker": str(marker_path),
                },
                "marker_sha256": digest(marker_data),
                "execution": prepared,
                "started_at": _now(),
                "setup_elapsed_seconds": 0.0,
                "action_elapsed_seconds": 0.0,
                "stages": stages,
                "attempts": [],
                "cleanup": "not-requested" if outcome == "keep" else "pending",
            }
            _atomic_write(paths["session"], canonical_json(session))
            return session, paths["session"]
        except BaseException:
            if created_root:
                shutil.rmtree(root, ignore_errors=True)
            if created_evidence:
                shutil.rmtree(paths["evidence"], ignore_errors=True)
            if created_plan and paths["plan"].is_file() and not paths["plan"].is_symlink():
                paths["plan"].unlink()
            if created_lock and paths["lock"].is_file() and not paths["lock"].is_symlink():
                paths["lock"].unlink()
            raise

    def resume(self, session_path: Path) -> dict[str, Any]:
        with _exclusive_session_lock(Path(str(session_path).removesuffix(".json") + ".lock")):
            return self._resume_unlocked(session_path)

    def _resume_unlocked(self, session_path: Path) -> dict[str, Any]:
        session = load_session(session_path)
        if session["package"]["selector"] != self.entry.selector or session["package"]["sha256"] != self.entry.package_sha256:
            raise Refusal("session package differs from the selected catalog package")
        plan = load_plan(Path(session["plan"]["path"]))
        if plan.sha256 != session["plan"]["sha256"]:
            raise Refusal("session plan hash differs")
        _verify_session_plan_paths(session, session_path, plan.document)
        if (
            plan.document["question"]["selector"] != self.entry.selector
            or plan.document["question"]["package_sha256"] != self.entry.package_sha256
            or plan.document["execution"]["paths"]["root"] != session["paths"]["root"]
            or plan.document["execution"]["outcome"] != session["outcome"]
        ):
            raise Refusal("session plan does not bind the active run")
        if session["state"] == "complete" and session.get("cleanup") == "complete":
            return session
        if Path(session["temper"]["path"]) != self.temper:
            raise Refusal("requested Temper executable differs from the session")
        if session["machine_facts"] != self.facts.document:
            raise Refusal("machine facts changed since consent")
        if session["field_kit_runtime"] != _runtime_identity():
            raise Refusal("Field Kit runtime changed since consent")
        root = Path(session["paths"]["root"])
        marker = Path(session["paths"]["marker"])
        if not root.is_dir() or root.is_symlink() or marker.is_symlink() or not marker.is_file():
            raise Refusal("session-owned root or marker is absent")
        if digest(marker.read_bytes()) != session["marker_sha256"]:
            raise Refusal("session ownership marker differs")
        if _file_digest(Path(session["temper"]["path"])) != session["temper"]["sha256"]:
            raise Refusal("Temper binary changed during the Field Kit session")
        if _file_digest(Path(session["field_kit_runtime"]["python_path"])) != session["field_kit_runtime"]["python_sha256"]:
            raise Refusal("Python runtime changed during the Field Kit session")
        self._verify_materialized(session)
        self._enforce_evidence_ceiling(session)
        _collect_action_evidence(
            session,
            self.entry.package["investigation"],
            self.entry.package["profile"]["layout"],
        )
        recovered_attempt = False
        for attempt in session.get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("state") == "running":
                if attempt.get("kind") == "question-action":
                    running_steps = [
                        step for step in attempt.get("steps", [])
                        if isinstance(step, dict) and step.get("state") == "running"
                    ]
                    running_step = running_steps[-1] if running_steps else None
                    charged = float(
                        running_step.get("timeout_seconds", 0)
                        if running_step is not None
                        else attempt.get("timeout_seconds", 0)
                    )
                    session["action_elapsed_seconds"] = round(
                        float(session.get("action_elapsed_seconds", 0)) + max(charged, 0),
                        6,
                    )
                    prior_step_elapsed = sum(
                        float(step.get("elapsed_seconds", 0))
                        for step in attempt.get("steps", [])
                        if isinstance(step, dict) and step is not running_step
                    )
                    attempt["elapsed_seconds"] = round(
                        prior_step_elapsed + max(charged, 0),
                        6,
                    )
                    attempt["budget_accounting"] = "full timeout charged after interruption"
                    if running_step is not None:
                        running_step["state"] = "interrupted"
                        running_step["elapsed_seconds"] = max(charged, 0)
                        running_step["finished_at"] = _now()
                        running_step["budget_accounting"] = (
                            "full timeout charged after interruption"
                        )
                        running_step["failure"] = {
                            "category": "interrupted",
                            "message": "no committed step result was present when the session resumed",
                        }
                elif attempt.get("kind") == "stage" and attempt.get("operation") != "outcome":
                    charged = float(attempt.get("timeout_seconds", 0))
                    session["setup_elapsed_seconds"] = round(
                        float(session.get("setup_elapsed_seconds", 0)) + max(charged, 0),
                        6,
                    )
                    attempt["elapsed_seconds"] = max(charged, 0)
                    attempt["budget_accounting"] = "full timeout charged after interruption"
                attempt["state"] = "interrupted"
                attempt["finished_at"] = _now()
                attempt["failure"] = {
                    "category": "interrupted",
                    "message": "no committed result was present when the session resumed",
                }
                recovered_attempt = True
        if recovered_attempt:
            _atomic_write(session_path, canonical_json(session))
        return session

    def run(self, session_path: Path, confirm_restore: Callable[[], bool]) -> dict[str, Any]:
        with _exclusive_session_lock(Path(str(session_path).removesuffix(".json") + ".lock")):
            return self._run_unlocked(session_path, confirm_restore)

    def _run_unlocked(self, session_path: Path, confirm_restore: Callable[[], bool]) -> dict[str, Any]:
        session = self._resume_unlocked(session_path)
        if session["state"] == "complete":
            if session["outcome"] == "restore" and session["cleanup"] != "complete":
                if not confirm_restore():
                    raise Refusal("restore confirmation declined; the dedicated root was retained")
                self._restore(session, session_path)
            return session
        if session["state"] == "ready-to-finish":
            raise Refusal("adaptive final validation is complete; finish the exact session")
        for index, stage in enumerate(session["stages"]):
            if stage["operation"] == "outcome":
                continue
            if stage["state"] == "complete":
                continue
            self._run_stage(session, session_path, index)
        session["state"] = "setup-complete"
        _atomic_write(session_path, canonical_json(session))
        completed_actions = [
            attempt for attempt in session["attempts"]
            if attempt.get("kind") == "question-action" and attempt.get("state") == "complete"
        ]
        if not completed_actions:
            self._run_question_action(
                session,
                session_path,
                initial_action_request(self.entry.package["investigation"]),
            )
        session["state"] = "measurement-complete"
        _atomic_write(session_path, canonical_json(session))
        if self.entry.package["kind"] == "bounded-adaptive":
            session["state"] = "awaiting-action"
            _atomic_write(session_path, canonical_json(session))
            return session
        return self._finish_session(session, session_path, confirm_restore)

    def submit_action(self, session_path: Path, request: object) -> dict[str, Any]:
        with _exclusive_session_lock(Path(str(session_path).removesuffix(".json") + ".lock")):
            return self._submit_action_unlocked(session_path, request)

    def _submit_action_unlocked(self, session_path: Path, request: object) -> dict[str, Any]:
        session = self._resume_unlocked(session_path)
        if self.entry.package["kind"] != "bounded-adaptive":
            raise Refusal("fixed questions do not accept adaptive action proposals")
        if session["state"] != "awaiting-action":
            raise Refusal("session is not awaiting an adaptive action")
        if session.get("protocol_evidence", {}).get("safe_to_cleanup") is False:
            raise Refusal("owned process shutdown was not established; inspect the retained evidence before another action")
        investigation = self.entry.package["investigation"]
        if investigation["action_selection"] == "result-directed":
            proposed = propose_action(investigation, request, session["attempts"])
            candidate = {
                "id": proposed.document["id"],
                "parameters": proposed.document["parameters"],
            }
            allowed = session.get("allowed_actions")
            if not isinstance(allowed, list) or candidate not in allowed:
                raise Refusal("action was not issued by the bound package controller")
        attempt = self._run_question_action(session, session_path, request)
        if attempt["action"]["id"] == self.entry.package["investigation"]["final_validation_action"]:
            session["state"] = "ready-to-finish"
        else:
            session["state"] = "awaiting-action"
        _atomic_write(session_path, canonical_json(session))
        return session

    def finish(self, session_path: Path, confirm_restore: Callable[[], bool]) -> dict[str, Any]:
        with _exclusive_session_lock(Path(str(session_path).removesuffix(".json") + ".lock")):
            return self._finish_unlocked(session_path, confirm_restore)

    def _finish_unlocked(self, session_path: Path, confirm_restore: Callable[[], bool]) -> dict[str, Any]:
        session = self._resume_unlocked(session_path)
        if self.entry.package["kind"] != "bounded-adaptive":
            raise Refusal("fixed questions finish during their single run")
        if session["state"] != "ready-to-finish":
            raise Refusal("adaptive session has no completed final-validation action")
        return self._finish_session(session, session_path, confirm_restore)

    def _finish_session(
        self,
        session: dict[str, Any],
        session_path: Path,
        confirm_restore: Callable[[], bool],
    ) -> dict[str, Any]:
        if session.get("protocol_evidence", {}).get("safe_to_cleanup") is False:
            raise Refusal("owned process shutdown was not established; retain the installation and inspect its protocol evidence before cleanup")
        outcome_index = next(
            index for index, stage in enumerate(session["stages"])
            if stage["operation"] == "outcome"
        )
        outcome_stage = session["stages"][outcome_index]
        if outcome_stage["state"] != "complete":
            if session["outcome"] == "restore" and not confirm_restore():
                raise Refusal("restore confirmation declined; the dedicated root was retained")
            self._run_stage(session, session_path, outcome_index)
        session["state"] = "stages-complete"
        session["finished_at"] = _now()
        _atomic_write(session_path, canonical_json(session))
        report_path = Path(session["paths"]["report"])
        report = render_report(session, self.entry)
        _write_new_or_same(report_path, report)
        session["report"] = {"path": str(report_path), "sha256": digest(report)}
        self._enforce_evidence_ceiling(session)
        session["state"] = "complete"
        _atomic_write(session_path, canonical_json(session))
        if session["outcome"] == "restore":
            self._restore(session, session_path)
        return session

    def _verify_materialized(self, session: dict[str, Any]) -> None:
        package_root = Path(session["paths"]["package_root"])
        expected = {"package.json": self.entry.package_data, **self.entry.files}
        for relative, data in expected.items():
            path = package_root / relative
            if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != digest(data):
                raise Refusal(f"materialized package file differs: {relative}")
        inputs_path = Path(session["paths"]["software_lock"]).parent
        expected = self._prepare_execution(package_root, inputs_path, dry_run=True)
        for key in ("profile", "execution_digest", "lock_sha256", "layouts", "inputs"):
            if expected[key] != session["execution"][key]:
                raise Refusal("session execution inputs differ from the supplied lock")
        for name, identity in expected["inputs"].items():
            path = inputs_path / name
            if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != identity["sha256"]:
                raise Refusal(f"materialized execution input differs: {name}")

    def _prepare_execution(self, package_root: Path, inputs_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
        from .execution import prepare_execution

        prepared = prepare_execution(
            self.temper, package_root / self.entry.package["execution_lock"]["path"],
            inputs_path, dry_run=dry_run, runner=self.runner,
        )
        if prepared["layouts"] != [self.entry.package["profile"]["layout"]] or prepared["profile"] != self.entry.package["mechanics"]["mode"]:
            raise Refusal("exported execution does not match the question's selected profile and layout")
        return prepared

    def _run_stage(self, session: dict[str, Any], session_path: Path, index: int) -> None:
        stage = session["stages"][index]
        attempt_id = f"attempt-{len(session['attempts']) + 1:04d}"
        arguments = self._arguments(session, stage["operation"])
        self.progress(f"FIELD-KIT running stage={stage['id']} operation={stage['operation']}")
        attempt = {
            "id": attempt_id,
            "kind": "stage",
            "stage": stage["id"],
            "operation": stage["operation"],
            "reason": "initial" if not stage["attempts"] else "resume-after-failure",
            "candidate": {},
            "arguments": list(arguments),
            "state": "running",
            "started_at": _now(),
        }
        session["attempts"].append(attempt)
        stage["attempts"].append(attempt_id)
        started = time.monotonic()
        timeout = self._stage_timeout(session, stage["operation"])
        attempt["timeout_seconds"] = timeout
        _atomic_write(session_path, canonical_json(session))
        result = self.runner(arguments, timeout) if arguments else CommandResult(b"RESULT field-kit-outcome kept\n", b"", 0)
        elapsed = time.monotonic() - started
        attempt["elapsed_seconds"] = round(elapsed, 6)
        attempt["finished_at"] = _now()
        attempt["returncode"] = result.returncode
        if stage["operation"] != "outcome":
            session["setup_elapsed_seconds"] = round(float(session.get("setup_elapsed_seconds", 0)) + elapsed, 6)
        evidence_root = Path(session["paths"]["evidence"])
        stage_path = evidence_root / "stages" / f"{stage['id']}.stdout"
        stderr_path = evidence_root / "stages" / f"{stage['id']}.stderr"
        attempt_stdout = evidence_root / "attempts" / f"{attempt_id}.stdout"
        attempt_stderr = evidence_root / "attempts" / f"{attempt_id}.stderr"
        _atomic_write(attempt_stdout, result.stdout)
        _atomic_write(attempt_stderr, result.stderr)
        attempt["evidence"] = {
            "stdout": str(attempt_stdout),
            "stdout_sha256": digest(result.stdout),
            "stderr": str(attempt_stderr),
            "stderr_sha256": digest(result.stderr),
        }
        try:
            self._enforce_evidence_ceiling(session)
        except Refusal as error:
            attempt["state"] = "failed"
            attempt["failure"] = {"category": "boundary", "message": str(error)}
            _atomic_write(session_path, canonical_json(session))
            raise
        if result.returncode != 0:
            _atomic_write(stage_path.with_suffix(".stdout.failed"), result.stdout)
            _atomic_write(stderr_path.with_suffix(".stderr.failed"), result.stderr)
            attempt["state"] = "failed"
            attempt["failure"] = {
                "category": "operational",
                "message": str(CommandFailure(arguments, result)),
            }
            _atomic_write(session_path, canonical_json(session))
            raise CommandFailure(arguments, result)
        try:
            self._validate_stage(session, stage["operation"], result.stdout)
        except (OSError, ValueError) as error:
            attempt["state"] = "failed"
            attempt["failure"] = {
                "category": "measurement",
                "message": str(error),
            }
            _atomic_write(session_path, canonical_json(session))
            raise
        _atomic_write(stage_path, result.stdout)
        _atomic_write(stderr_path, result.stderr)
        try:
            self._enforce_evidence_ceiling(session)
        except Refusal as error:
            attempt["state"] = "failed"
            attempt["failure"] = {"category": "boundary", "message": str(error)}
            _atomic_write(session_path, canonical_json(session))
            raise
        attempt["state"] = "complete"
        stage.update({
            "state": "complete", "completed_at": _now(),
            "stdout_sha256": digest(result.stdout), "stderr_sha256": digest(result.stderr),
        })
        if stage["operation"] == "config-apply":
            session["generation"] = _parse_generation(result.stdout)
        elif stage["operation"] == "material-bind":
            session["binding"] = {"sha256": digest(result.stdout)}
        _atomic_write(session_path, canonical_json(session))

    def _run_question_action(
        self,
        session: dict[str, Any],
        session_path: Path,
        request: object,
    ) -> dict[str, Any]:
        action = propose_action(
            self.entry.package["investigation"],
            request,
            session["attempts"],
        )
        definition = next(
            item for item in self.entry.package["investigation"]["actions"]
            if item["id"] == action.document["id"]
        )
        action_timeout = self._action_timeout(session, action.document["id"])
        evidence_used = self._enforce_evidence_ceiling(session)
        action_evidence_max = sum(
            step["evidence_bytes_max"] for step in definition["steps"]
        )
        evidence_max = self.entry.package["cost"]["evidence_bytes_max"]
        if evidence_used + action_evidence_max > evidence_max:
            raise Refusal(
                "declared evidence byte ceiling has insufficient room for this action"
            )
        attempt_id = f"attempt-{len(session['attempts']) + 1:04d}"
        action_root = Path(session["paths"]["evidence"]) / "actions" / attempt_id
        action_path = action_root / "action.json"
        _atomic_write(action_path, action.data)
        self.progress(f"FIELD-KIT running action={action.document['id']} attempt={attempt_id}")
        attempt = {
            "id": attempt_id,
            "kind": "question-action",
            "stage": "question-action",
            "operation": "live-protocol",
            "reason": action.document["reason"],
            "candidate": action.document["parameters"],
            "changes": action.document["changes"],
            "action": {
                "id": action.document["id"],
                "kind": action.document["kind"],
                "path": str(action_path),
                "sha256": action.sha256,
            },
            "timeout_seconds": action_timeout,
            "steps": [],
            "artifacts": {},
            "state": "running",
            "started_at": _now(),
        }
        session["attempts"].append(attempt)
        _atomic_write(session_path, canonical_json(session))
        action_started = time.monotonic()
        artifacts: dict[str, dict[str, Any]] = {}
        terminal_report: dict[str, Any] | None = None
        terminal_report_record: dict[str, str] | None = None
        for step_index, step_definition in enumerate(definition["steps"], 1):
            elapsed_before_step = time.monotonic() - action_started
            timeout = self._step_timeout(
                session,
                definition,
                step_definition,
                elapsed_before_step,
            )
            step_root = action_root / "steps" / step_definition["id"]
            step_path = step_root / "step.json"
            report_path = step_root / "report.json"
            log_dir = step_root / "logs"
            artifact_root = action_root / "artifacts"
            for name in step_definition["produces"]:
                output = artifact_root / name
                if output.exists() or output.is_symlink():
                    raise Refusal(f"prepared artifact output already exists: {output}")
            step = build_step(
                action=action.document,
                action_sha256=action.sha256,
                step=step_definition,
                step_index=step_index,
                plan_sha256=session["plan"]["sha256"],
                inputs=artifacts,
                artifact_root=artifact_root,
            )
            _atomic_write(step_path, step.data)
            arguments = self._action_arguments(
                session,
                action_path,
                step_path,
                report_path,
                log_dir,
            )
            self.progress(
                f"FIELD-KIT running action={action.document['id']} "
                f"step={step_definition['id']} attempt={attempt_id}"
            )
            step_attempt = {
                "id": step_definition["id"],
                "index": step_index,
                "kind": step_definition["kind"],
                "step": {"path": str(step_path), "sha256": step.sha256},
                "arguments": list(arguments),
                "timeout_seconds": timeout,
                "state": "running",
                "started_at": _now(),
            }
            attempt["steps"].append(step_attempt)
            _atomic_write(session_path, canonical_json(session))
            evidence_before_runner = self._enforce_evidence_ceiling(session)
            started = time.monotonic()
            result = self.runner(arguments, timeout)
            elapsed = time.monotonic() - started
            session["action_elapsed_seconds"] = round(
                float(session.get("action_elapsed_seconds", 0)) + elapsed,
                6,
            )
            step_attempt["elapsed_seconds"] = round(elapsed, 6)
            step_attempt["finished_at"] = _now()
            step_attempt["returncode"] = result.returncode
            stdout_path = step_root / "stdout"
            stderr_path = step_root / "stderr"
            _atomic_write(stdout_path, result.stdout)
            _atomic_write(stderr_path, result.stderr)
            step_attempt["evidence"] = {
                "stdout": str(stdout_path),
                "stdout_sha256": digest(result.stdout),
                "stderr": str(stderr_path),
                "stderr_sha256": digest(result.stderr),
            }
            try:
                evidence_after_runner = self._enforce_evidence_ceiling(session)
                if (
                    evidence_after_runner - evidence_before_runner
                    > step_definition["evidence_bytes_max"]
                ):
                    raise Refusal(
                        "question action step exceeded its evidence byte ceiling"
                    )
            except Refusal as error:
                failure = {"category": "boundary", "message": str(error)}
                step_attempt["state"] = "failed"
                step_attempt["failure"] = failure
                attempt["state"] = "failed"
                attempt["failure"] = failure
                attempt["elapsed_seconds"] = round(time.monotonic() - action_started, 6)
                attempt["finished_at"] = _now()
                _atomic_write(session_path, canonical_json(session))
                raise
            if result.returncode != 0:
                failure = {
                    "category": "operational",
                    "message": str(CommandFailure(arguments, result)),
                }
                step_attempt["state"] = "failed"
                step_attempt["failure"] = failure
                attempt["state"] = "failed"
                attempt["failure"] = failure
                attempt["elapsed_seconds"] = round(time.monotonic() - action_started, 6)
                attempt["finished_at"] = _now()
                _atomic_write(session_path, canonical_json(session))
                raise CommandFailure(arguments, result)
            try:
                report, report_data, produced, terminal, next_actions = self._validate_step_report(
                    session,
                    action.sha256,
                    step,
                    step_definition,
                    report_path,
                    artifact_root,
                    artifacts,
                    step_index == len(definition["steps"]),
                )
            except (OSError, ValueError) as error:
                failure = {"category": "measurement", "message": str(error)}
                step_attempt["state"] = "failed"
                step_attempt["failure"] = failure
                attempt["state"] = "failed"
                attempt["failure"] = failure
                attempt["elapsed_seconds"] = round(time.monotonic() - action_started, 6)
                attempt["finished_at"] = _now()
                _atomic_write(session_path, canonical_json(session))
                raise
            artifacts.update(produced)
            attempt["artifacts"] = artifacts
            report_record = {"path": str(report_path), "sha256": digest(report_data)}
            step_attempt["report"] = report_record
            step_attempt["state"] = "complete"
            if terminal:
                terminal_report = report
                terminal_report_record = report_record
                break
            _atomic_write(session_path, canonical_json(session))
        if terminal_report is None or terminal_report_record is None:
            raise Refusal("question action ended without a terminal step result")
        protocol = terminal_report["protocol"]
        answers = terminal_report["answers"]
        attempt["next_actions"] = next_actions
        attempt["state"] = "complete"
        attempt["elapsed_seconds"] = round(time.monotonic() - action_started, 6)
        attempt["finished_at"] = _now()
        attempt["protocol_report"] = terminal_report_record
        session["protocol_evidence"] = protocol
        session["answers"] = answers
        if self.entry.package["investigation"]["action_selection"] == "result-directed":
            session["allowed_actions"] = next_actions
        session.setdefault("action_results", []).append({
            "attempt": attempt_id,
            "action_sha256": action.sha256,
            "reports": [item["report"] for item in attempt["steps"]],
            "artifacts": {
                name: item["artifact"] for name, item in sorted(artifacts.items())
            },
            "answers": answers,
            "next_actions": next_actions,
        })
        _atomic_write(session_path, canonical_json(session))
        return attempt

    def _stage_timeout(self, session: dict[str, Any], operation: str) -> float:
        if operation == "outcome":
            return 15 * 60
        maximum = float(self.entry.package["cost"]["setup_minutes_max"] * 60)
        remaining = maximum - float(session.get("setup_elapsed_seconds", 0))
        if remaining <= 0:
            raise Refusal("declared setup-time bound is exhausted; renewed consent is required")
        return remaining

    def _action_timeout(self, session: dict[str, Any], action_id: str) -> float:
        investigation = self.entry.package["investigation"]
        definition = next(
            (item for item in investigation["actions"] if item["id"] == action_id),
            None,
        )
        if definition is None:
            raise Refusal(f"unknown approved action {action_id!r}")
        per_action = float(definition["runtime_minutes_max"] * 60)
        total = float(investigation["total_runtime_minutes_max"] * 60)
        remaining = total - float(session.get("action_elapsed_seconds", 0))
        if remaining <= 0:
            raise Refusal("declared question-action time bound is exhausted; renewed consent is required")
        return min(per_action, remaining)

    def _step_timeout(
        self,
        session: dict[str, Any],
        action: dict[str, Any],
        step: dict[str, Any],
        action_elapsed_seconds: float,
    ) -> float:
        total = float(self.entry.package["investigation"]["total_runtime_minutes_max"] * 60)
        total_remaining = total - float(session.get("action_elapsed_seconds", 0))
        action_remaining = float(action["runtime_minutes_max"] * 60) - action_elapsed_seconds
        step_maximum = float(step["runtime_minutes_max"] * 60)
        remaining = min(total_remaining, action_remaining, step_maximum)
        if remaining <= 0:
            raise Refusal("declared question-action time bound is exhausted; renewed consent is required")
        return remaining

    def _arguments(self, session: dict[str, Any], operation: str) -> list[str]:
        package = self.entry.package
        mechanics = package["mechanics"]
        root = session["paths"]["root"]
        package_root = Path(session["paths"]["package_root"])
        software_lock = session["paths"]["software_lock"]
        inputs_path = Path(software_lock).parent
        manifest = inputs_path / "manifest.yaml"
        manifest_lock = inputs_path / "manifest.lock.yaml"
        temper = session["temper"]["path"]
        common = ["--root", root]
        if operation == "software-install":
            return [temper, "software", "install", *common, "--installation", mechanics["installation"], "--lock", software_lock]
        if operation == "model-fetch":
            return [temper, "fetch", package["profile"]["layout"], *common, "--manifest", str(manifest), "--lock", str(manifest_lock)]
        if operation == "config-apply":
            return [temper, "apply", *common, "--manifest", str(manifest), "--lock", str(manifest_lock), "--mode", mechanics["mode"]]
        if operation == "software-check":
            return [temper, "software", "check", *common, "--installation", mechanics["installation"], "--lock", software_lock]
        if operation == "artifact-check":
            return [temper, "check", *common, "--manifest", str(manifest), "--lock", str(manifest_lock), "--mode", mechanics["mode"], "--verify"]
        if operation == "material-bind":
            return [
                temper, "field-kit", "bind", *common, "--manifest-lock", str(manifest_lock),
                "--generation", session["generation"], "--installation", f"{mechanics['installation']}={software_lock}",
            ]
        if operation == "outcome":
            if session["outcome"] == "keep":
                return []
            return [temper, "software", "remove", *common, "--installation", mechanics["installation"], "--lock", software_lock]
        raise Refusal(f"unsupported stage operation: {operation}")

    def _action_arguments(
        self,
        session: dict[str, Any],
        action_path: Path,
        step_path: Path,
        report_path: Path,
        log_dir: Path,
    ) -> list[str]:
        package = self.entry.package
        mechanics = package["mechanics"]
        return [
            sys.executable,
            str(Path(session["paths"]["package_root"]) / mechanics["runner"]["path"]),
            "--action", str(action_path),
            "--step", str(step_path),
            "--temper", session["temper"]["path"],
            "--root", session["paths"]["root"],
            "--software-lock", session["paths"]["software_lock"],
            "--execution-lock", str(Path(session["paths"]["package_root"]) / package["execution_lock"]["path"]),
            "--request-defaults", str(Path(session["paths"]["software_lock"]).parent / "request-defaults.json"),
            "--manifest-lock", str(Path(session["paths"]["software_lock"]).parent / "manifest.lock.yaml"),
            "--generation", session["generation"],
            "--installation", mechanics["installation"],
            "--model", package["profile"]["layout"],
            "--listen", PROBE_LISTEN,
            "--report", str(report_path),
            "--log-dir", str(log_dir),
            "--field-kit-runtime", str(Path(__file__).resolve().parents[1]),
            "--session", session["paths"]["session"],
            "--outcome", session["outcome"],
        ]

    def _validate_stage(
        self,
        session: dict[str, Any],
        operation: str,
        output: bytes,
    ) -> None:
        text = output.decode(errors="replace")
        required = {
            "software-install": "RESULT software-install ",
            "model-fetch": "RESULT fetch ",
            "config-apply": "RESULT apply ",
            "software-check": "RESULT software-check exact ",
            "artifact-check": "RESULT check ok ",
            "material-bind": "schema: temper-field-kit-binding/v1\n",
            "outcome": "RESULT ",
        }
        if operation in required and required[operation] not in text:
            raise Refusal(f"{operation} returned an unexpected result")
        if operation == "config-apply":
            _parse_generation(output)

    def _validate_step_report(
        self,
        session: dict[str, Any],
        action_sha256: str,
        step: Any,
        definition: dict[str, Any],
        path: Path,
        artifact_root: Path,
        prior_artifacts: dict[str, dict[str, Any]],
        final_step: bool,
    ) -> tuple[dict[str, Any], bytes, dict[str, dict[str, Any]], bool, object]:
        if path.is_symlink() or not path.is_file():
            raise Refusal("question action step did not write its report")
        data = path.read_bytes()
        if len(data) > 1024 * 1024:
            raise Refusal("question action step report is too large")
        report = json.loads(data)
        produced, terminal, answers = validate_step_result(
            report,
            report_data=data,
            action_sha256=action_sha256,
            step=step,
            definition=definition,
            artifact_root=artifact_root,
            prior_artifacts=prior_artifacts,
            final_step=final_step,
        )
        assert isinstance(report, dict)
        _validate_protocol_material(
            report,
            schema=session["package"]["protocol"]["schema"],
            model=self.entry.package["profile"]["layout"],
            initial_generation=session["generation"],
            prior_artifacts=prior_artifacts,
            consumed=definition["consumes"],
            message="question action step did not complete for the bound model and generation",
        )
        if terminal:
            validate_answers(answers, self.entry.package["report"]["answer_fields"])
        next_actions = _validate_next_actions(
            self.entry.package["investigation"],
            step.document["action"]["id"],
            report.get("next_actions"),
            session["attempts"],
            terminal,
        )
        return report, data, produced, terminal, next_actions

    def _restore(self, session: dict[str, Any], session_path: Path) -> None:
        root = Path(session["paths"]["root"])
        marker = Path(session["paths"]["marker"])
        if marker.is_symlink() or not marker.is_file() or digest(marker.read_bytes()) != session["marker_sha256"]:
            raise Refusal("restore refused because the ownership marker differs")
        if root == Path(root.anchor) or root.is_symlink() or not root.is_dir():
            raise Refusal("restore refused because the root is unsafe")
        shutil.rmtree(root)
        session["cleanup"] = "complete"
        session["cleaned_at"] = _now()
        _atomic_write(session_path, canonical_json(session))


def _parse_generation(output: bytes) -> str:
    for field in output.decode(errors="replace").split():
        if field.startswith("generation="):
            generation = field.removeprefix("generation=")
            if GENERATION.fullmatch(generation):
                return generation
    raise Refusal("Temper apply output has no exact generation")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _validate_next_actions(
    investigation: dict[str, Any],
    action_id: str,
    value: object,
    attempts: Sequence[dict[str, Any]],
    terminal: bool,
) -> object:
    """Validate the package controller's exact next-action frontier."""
    if not terminal:
        if value is not None:
            raise Refusal("a non-terminal step cannot select the next action")
        return None
    if investigation["action_selection"] != "result-directed":
        if value is not None:
            raise Refusal("this investigation does not permit result-directed actions")
        return None
    if not isinstance(value, list):
        raise Refusal("result-directed action result must publish a next-action list")
    normalized = [validate_action_candidate(investigation, item) for item in value]
    keys = [canonical_json(item) for item in normalized]
    if keys != sorted(set(keys)):
        raise Refusal("next-action candidates must be sorted and unique")
    final_action = investigation["final_validation_action"]
    if action_id == final_action:
        if normalized:
            raise Refusal("final validation cannot publish another action")
        return normalized
    if not normalized:
        raise Refusal("result-directed measurement must publish an actionable frontier")
    for item in normalized:
        propose_action(
            investigation,
            {
                "id": item["id"],
                "parameters": item["parameters"],
                "reason": "package-controller candidate validation",
            },
            attempts,
        )
    return normalized


def load_session(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"session is not a regular file: {path}")
    data = path.read_bytes()
    if len(data) > 1024 * 1024:
        raise Refusal("session exceeds 1 MiB")
    try:
        document = json.loads(data)
    except json.JSONDecodeError as error:
        raise Refusal(f"invalid session JSON: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != SESSION_SCHEMA or canonical_json(document) != data:
        raise Refusal("session is not canonical field-kit-session/v2")
    if document.get("state") not in {
        "running", "setup-complete", "measurement-complete", "awaiting-action",
        "ready-to-finish", "stages-complete", "complete",
    }:
        raise Refusal("session has an invalid state")
    return document


def _validate_protocol_material(
    report: dict[str, Any],
    *,
    schema: str,
    model: str,
    initial_generation: str,
    prior_artifacts: dict[str, dict[str, Any]],
    consumed: list[str],
    message: str,
) -> None:
    protocol = report.get("protocol")
    allowed_generations = {initial_generation}
    allowed_generations.update(
        bound_material_generations(prior_artifacts, consumed, model)
    )
    if (
        not isinstance(protocol, dict)
        or protocol.get("schema") != schema
        or protocol.get("status") != "complete"
        or protocol.get("model") != model
        or protocol.get("generation") not in allowed_generations
    ):
        raise Refusal(message)


def _collect_action_evidence(
    session: dict[str, Any],
    investigation: dict[str, Any],
    model: str,
) -> list[dict[str, Any]]:
    """Verify and collect every committed question-action document and report."""
    evidence_root = Path(session["paths"]["evidence"])
    collected: list[dict[str, Any]] = []
    expected_results: list[dict[str, Any]] = []
    question_attempts_seen: list[dict[str, Any]] = []
    action_number = 0
    for attempt in session.get("attempts", []):
        if not isinstance(attempt, dict) or attempt.get("kind") != "question-action":
            continue
        question_attempts_seen.append(attempt)
        action_number += 1
        attempt_id = attempt.get("id")
        if not isinstance(attempt_id, str):
            raise Refusal("question-action attempt has no ID")
        action_root = evidence_root / "actions" / attempt_id
        action_record = attempt.get("action")
        if not isinstance(action_record, dict):
            raise Refusal(f"question-action {attempt_id} has no action identity")
        action_path = action_root / "action.json"
        if action_record.get("path") != str(action_path):
            raise Refusal(f"question-action {attempt_id} action path differs")
        action_data = _read_hashed_evidence(
            action_path,
            action_record.get("sha256"),
            64 * 1024,
            f"question-action {attempt_id} action",
        )
        try:
            action_document = json.loads(action_data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Refusal(f"question-action {attempt_id} action JSON is invalid") from error
        if (
            not isinstance(action_document, dict)
            or canonical_json(action_document) != action_data
            or action_document.get("schema") != ACTION_SCHEMA
            or action_document.get("attempt") != action_number
            or action_document.get("id") != action_record.get("id")
            or action_document.get("kind") != action_record.get("kind")
            or action_document.get("parameters") != attempt.get("candidate")
            or action_document.get("changes") != attempt.get("changes")
            or action_document.get("reason") != attempt.get("reason")
        ):
            raise Refusal(f"question-action {attempt_id} action document differs")
        definition = next(
            (item for item in investigation["actions"] if item["id"] == action_record.get("id")),
            None,
        )
        if definition is None:
            raise Refusal(f"question-action {attempt_id} is not declared by the investigation")
        artifacts: dict[str, dict[str, Any]] = {}
        step_items: list[dict[str, Any]] = []
        terminal_report: dict[str, Any] | None = None
        terminal_report_record: dict[str, str] | None = None
        terminal_next_actions: object = None
        step_attempts = attempt.get("steps")
        if not isinstance(step_attempts, list) or len(step_attempts) > len(definition["steps"]):
            raise Refusal(f"question-action {attempt_id} step ledger is invalid")
        for offset, step_attempt in enumerate(step_attempts):
            step_definition = definition["steps"][offset]
            step_id = step_definition["id"]
            if not isinstance(step_attempt, dict) or step_attempt.get("id") != step_id:
                raise Refusal(f"question-action {attempt_id} step order differs")
            step_root = action_root / "steps" / step_id
            step_path = step_root / "step.json"
            step_record = step_attempt.get("step")
            if not isinstance(step_record, dict) or step_record.get("path") != str(step_path):
                raise Refusal(f"question-action {attempt_id} step path differs")
            step_data = _read_hashed_evidence(
                step_path,
                step_record.get("sha256"),
                256 * 1024,
                f"question-action {attempt_id} step {step_id}",
            )
            try:
                step_document = json.loads(step_data)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise Refusal(f"question-action {attempt_id} step JSON is invalid") from error
            expected_step = build_step(
                action=action_document,
                action_sha256=action_record["sha256"],
                step=step_definition,
                step_index=offset + 1,
                plan_sha256=session["plan"]["sha256"],
                inputs=artifacts,
                artifact_root=action_root / "artifacts",
            )
            if step_document != expected_step.document or step_data != expected_step.data:
                raise Refusal(f"question-action {attempt_id} step document differs")
            evidence = step_attempt.get("evidence")
            if evidence is not None:
                if not isinstance(evidence, dict) or set(evidence) != {
                    "stdout", "stdout_sha256", "stderr", "stderr_sha256",
                }:
                    raise Refusal(f"question-action {attempt_id} step evidence is invalid")
                for stream in ("stdout", "stderr"):
                    expected_path = step_root / stream
                    if evidence[stream] != str(expected_path):
                        raise Refusal(f"question-action {attempt_id} step {stream} path differs")
                    _read_hashed_evidence(
                        expected_path,
                        evidence[f"{stream}_sha256"],
                        step_definition["evidence_bytes_max"],
                        f"question-action {attempt_id} step {step_id} {stream}",
                    )
            step_item: dict[str, Any] = {
                "id": step_id,
                "state": step_attempt.get("state"),
                "step": step_document,
                "step_sha256": expected_step.sha256,
            }
            if step_attempt.get("state") == "complete":
                if evidence is None:
                    raise Refusal(f"completed question-action {attempt_id} step has no evidence")
                report_record = step_attempt.get("report")
                report_path = step_root / "report.json"
                if not isinstance(report_record, dict) or report_record.get("path") != str(report_path):
                    raise Refusal(f"question-action {attempt_id} step report path differs")
                report_data = _read_hashed_evidence(
                    report_path,
                    report_record.get("sha256"),
                    1024 * 1024,
                    f"question-action {attempt_id} step {step_id} report",
                )
                try:
                    report = json.loads(report_data)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise Refusal(f"question-action {attempt_id} step report JSON is invalid") from error
                produced, terminal, answers = validate_step_result(
                    report,
                    report_data=report_data,
                    action_sha256=action_record["sha256"],
                    step=ActionStep(step_document, step_data, expected_step.sha256),
                    definition=step_definition,
                    artifact_root=action_root / "artifacts",
                    prior_artifacts=artifacts,
                    final_step=offset == len(definition["steps"]) - 1,
                )
                _validate_protocol_material(
                    report,
                    schema=session["package"]["protocol"]["schema"],
                    model=model,
                    initial_generation=session["generation"],
                    prior_artifacts=artifacts,
                    consumed=step_definition["consumes"],
                    message=(
                        f"question-action {attempt_id} step did not complete "
                        "for bound material"
                    ),
                )
                artifacts.update(produced)
                step_item["report"] = report
                step_item["report_sha256"] = report_record["sha256"]
                if terminal:
                    terminal_next_actions = _validate_next_actions(
                        investigation,
                        action_document["id"],
                        report.get("next_actions"),
                        question_attempts_seen,
                        True,
                    )
                    terminal_report = report
                    terminal_report_record = report_record
                    if offset != len(step_attempts) - 1:
                        raise Refusal(f"question-action {attempt_id} continued after a terminal result")
            step_items.append(step_item)
        state = attempt.get("state")
        item: dict[str, Any] = {
            "attempt": attempt_id,
            "action": action_document,
            "action_sha256": action_record["sha256"],
            "state": state,
            "steps": step_items,
            "artifacts": {
                name: value["artifact"] for name, value in sorted(artifacts.items())
            },
        }
        if state == "complete":
            if terminal_report is None or terminal_report_record is None:
                raise Refusal(f"completed question-action {attempt_id} has no terminal result")
            if attempt.get("protocol_report") != terminal_report_record:
                raise Refusal(f"question-action {attempt_id} terminal report differs")
            if attempt.get("next_actions") != terminal_next_actions:
                raise Refusal(f"question-action {attempt_id} next-action frontier differs")
            expected_result = {
                "attempt": attempt_id,
                "action_sha256": action_record["sha256"],
                "reports": [step["report"] for step in step_attempts],
                "artifacts": item["artifacts"],
                "answers": terminal_report.get("answers"),
                "next_actions": terminal_next_actions,
            }
            expected_results.append(expected_result)
            item["terminal_report"] = terminal_report
            item["terminal_report_sha256"] = terminal_report_record["sha256"]
        if attempt.get("artifacts", {}) != artifacts:
            raise Refusal(f"question-action {attempt_id} artifact ledger differs")
        collected.append(item)
    if session.get("action_results", []) != expected_results:
        raise Refusal("session action result ledger differs from retained reports")
    if investigation["action_selection"] == "result-directed":
        expected_allowed = expected_results[-1]["next_actions"] if expected_results else None
        if session.get("allowed_actions") != expected_allowed:
            raise Refusal("session next-action frontier differs from the latest result")
    elif "allowed_actions" in session:
        raise Refusal("session has a next-action frontier for an operator-bounded question")
    if expected_results:
        latest_report = next(
            item["terminal_report"] for item in reversed(collected)
            if "terminal_report" in item
        )
        if (
            session.get("protocol_evidence") != latest_report.get("protocol")
            or session.get("answers") != latest_report.get("answers")
        ):
            raise Refusal("session answer differs from the latest completed question action")
    elif "protocol_evidence" in session or "answers" in session:
        raise Refusal("session has an answer without a completed question action")
    return collected


def _verify_session_plan_paths(
    session: dict[str, Any],
    session_path: Path,
    plan: dict[str, Any],
) -> None:
    expected = plan.get("execution", {}).get("paths", {})
    if not isinstance(expected, dict) or session_path != Path(str(expected.get("session", ""))):
        raise Refusal("session file path differs from the approved plan")
    session_paths = session.get("paths")
    if not isinstance(session_paths, dict):
        raise Refusal("session paths are invalid")
    for label in ("root", "plan", "session", "lock", "report", "evidence"):
        if session_paths.get(label) != expected.get(label):
            raise Refusal(f"session {label} path differs from the approved plan")


def _read_hashed_evidence(path: Path, expected: object, limit: int, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"{label} is absent or not a regular file")
    if path.stat().st_size > limit:
        raise Refusal(f"{label} exceeds {limit} bytes")
    data = path.read_bytes()
    if not isinstance(expected, str) or digest(data) != expected:
        raise Refusal(f"{label} hash differs")
    return data


def render_report(session: dict[str, Any], entry: QuestionPackage) -> bytes:
    facts = session["machine_facts"]
    lines = [
        "# Field Kit question report",
        "",
        f"- Question: {entry.package['question']}",
        f"- Question package: `{session['package']['selector']}`",
        f"- Package SHA-256: `{session['package']['sha256']}`",
        f"- Plan SHA-256: `{session['plan']['sha256']}`",
        f"- Field Kit runner SHA-256: `{session['package']['runner_sha256']}`",
        f"- Protocol: `{session['package']['protocol']['id']}@{session['package']['protocol']['revision']}` (`{session['package']['protocol']['schema']}`)",
        f"- Field Kit: `{session['field_kit_runtime']['version']}`",
        f"- Field Kit runtime SHA-256: `{session['field_kit_runtime']['sha256']}`",
        f"- Temper: `{session['temper']['version']}`",
        f"- Temper SHA-256: `{session['temper']['sha256']}`",
        f"- Python: `{session['field_kit_runtime']['python_version']}`",
        f"- Python SHA-256: `{session['field_kit_runtime']['python_sha256']}`",
        f"- Outcome: `{session['outcome']}`",
        f"- Started: `{session['started_at']}`",
        f"- Finished: `{session.get('finished_at', '')}`",
        f"- Question-action time used: `{session.get('action_elapsed_seconds', 0):.6f}` seconds",
        "",
        "## Answer",
        "",
    ]
    answers = session.get("answers", {})
    for field in entry.package["report"]["answer_fields"]:
        answer = answers.get(field, {"state": "unknown", "reason": "no valid protocol answer was retained"})
        details = []
        if "value" in answer:
            details.append(json.dumps(answer["value"], sort_keys=True, separators=(",", ":")))
        if answer.get("reason"):
            details.append(str(answer["reason"]))
        detail = "; ".join(details)
        lines.append(f"- `{field}`: **{answer['state']}**" + (f" — `{detail}`" if detail else ""))
    lines.extend([
        "",
        "## Machine",
        "",
        f"- Chip: `{facts['chip']}`",
        f"- Hardware model: `{facts['hardware_model']}`",
        f"- Physical memory: `{facts['physical_memory_bytes']}` bytes",
        f"- Wired limit: `{facts['wired_limit_mib']}` MiB",
        f"- OS build: `{facts['os_build']}`",
        "",
        "## Attempts",
        "",
    ])
    for attempt in session.get("attempts", []):
        failure = attempt.get("failure", {})
        suffix = f"; {failure.get('category')}: {failure.get('message')}" if failure else ""
        lines.append(
            f"- `{attempt['id']}` {attempt['stage']}: {attempt['state']} "
            f"({attempt.get('elapsed_seconds', 0):.6f}s){suffix}"
        )
    action_attempts = [
        attempt for attempt in session.get("attempts", [])
        if attempt.get("kind") == "question-action"
    ]
    if action_attempts:
        lines.extend([
            "",
            "## Question actions",
            "",
        ])
        for attempt in action_attempts:
            candidate = json.dumps(
                attempt.get("candidate", {}),
                sort_keys=True,
                separators=(",", ":"),
            )
            changes = json.dumps(
                attempt.get("changes", {}),
                sort_keys=True,
                separators=(",", ":"),
            )
            report = attempt.get("protocol_report", {})
            lines.append(
                f"- `{attempt['id']}` `{attempt['action']['id']}`: {attempt['state']}; "
                f"candidate `{candidate}`; changes `{changes}`; "
                f"action `{attempt['action']['sha256']}`; "
                f"report `{report.get('sha256', '')}`"
            )
            for step in attempt.get("steps", []):
                step_report = step.get("report", {})
                lines.append(
                    f"  - step `{step.get('id', '')}` ({step.get('kind', '')}): "
                    f"{step.get('state', '')}; report `{step_report.get('sha256', '')}`"
                )
            for name, artifact in sorted(attempt.get("artifacts", {}).items()):
                descriptor = artifact.get("artifact", {})
                lines.append(f"  - artifact `{name}`: `{descriptor.get('id', '')}`")
            if attempt.get("next_actions") is not None:
                rendered_next = json.dumps(
                    attempt["next_actions"],
                    sort_keys=True,
                    separators=(",", ":"),
                )
                lines.append(f"  - controller frontier: `{rendered_next}`")
    lines.extend([
        "",
        "## Stages",
        "",
    ])
    for stage in session["stages"]:
        lines.append(f"- `{stage['id']}` {stage['operation']}: {stage['state']} (`{stage.get('stdout_sha256', '')}`)")
    protocol = session.get("protocol_evidence")
    if isinstance(protocol, dict):
        resources = protocol.get("resources", [])
        max_swap = max((float(item.get("swap_growth_mib", 0)) for item in resources if isinstance(item, dict)), default=0.0)
        checks = protocol.get("checks", {})
        lines.extend([
            "",
            "## Latest question-action evidence",
            "",
            f"- Status: `{protocol.get('status')}`",
            f"- Schema: `{protocol.get('schema')}`",
            f"- Checks: {', '.join(sorted(checks)) if isinstance(checks, dict) else 'unavailable'}",
            f"- Maximum observed swap growth: `{max_swap:.3f}` MiB",
            "- " + EVIDENCE_DISCLOSURE,
        ])
    lines.extend([
        "",
        "## Boundary",
        "",
        entry.package["evidence_scope"],
        "",
        "This local report is evidence from one machine. It is not a model recommendation and was not uploaded.",
    ])
    return ("\n".join(lines) + "\n").encode()


def build_export(session_path: Path) -> tuple[bytes, dict[str, Any]]:
    session = load_session(session_path)
    if session["state"] != "complete" or not isinstance(session.get("report"), dict):
        raise Refusal("only a complete Field Kit session can be exported")
    report_path = Path(session["report"]["path"])
    plan_path = Path(session["plan"]["path"])
    if report_path.is_symlink() or not report_path.is_file():
        raise Refusal("session report is absent")
    plan = load_plan(plan_path)
    if plan.sha256 != session["plan"]["sha256"]:
        raise Refusal("session plan hash differs")
    _verify_session_plan_paths(session, session_path, plan.document)
    session_data = session_path.read_bytes()
    report_data = report_path.read_bytes()
    if digest(report_data) != session["report"]["sha256"]:
        raise Refusal("session report hash differs")
    action_evidence = _collect_action_evidence(
        session,
        plan.document["investigation"],
        plan.document["question"]["model"],
    )
    packet = {
        "schema": EXPORT_SCHEMA,
        "package": session["package"],
        "plan": plan.document,
        "plan_sha256": plan.sha256,
        "machine": session["machine_facts"],
        "action_evidence": action_evidence,
        "session": session_data.decode(),
        "report": report_data.decode(),
    }
    data = canonical_json(packet)
    summary = {
        "package": session["package"]["selector"],
        "machine": "retained",
        "protocol": "retained" if session.get("protocol_evidence") else "unavailable",
        "disclosure": EVIDENCE_DISCLOSURE,
        "bytes": len(data),
        "sha256": digest(data),
    }
    return data, summary
