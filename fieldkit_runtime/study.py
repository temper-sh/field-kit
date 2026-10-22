"""Study custody: approved scope, attempts, recovery and close. Research policy
stays with the agent; the approval document is built in approval.py."""
from __future__ import annotations
import datetime
import json
from pathlib import Path
import shutil
import time
from . import jobs, lockview, machine, records, recovery, storage, workloads
from .adapters import engines, interfaces
from .approval import candidate, identity
from .common import Refusal, canonical_json, digest, load_json, read_regular
from .context import DEFAULT
from .material import validate_variant
from .process import CommandFailure, atomic_write, exclusive_lock, runtime_identity
from .temper import locate

class Study:
    def __init__(self, repository, name, context=DEFAULT):
        self.repository = Path(repository).resolve()
        self.name = identity(name)
        self.context = context
        self.directory = self.repository / ".work" / "studies" / self.name
        if any(path.is_symlink() for path in (self.directory, self.directory.parent, self.directory.parent.parent)):
            raise Refusal("study directories must not be symlinks")

    def load(self):
        state = load_json(self.directory / "study.json", records.RUN_LIMIT)
        if state.get("schema") != records.STUDY_SCHEMA or state.get("id") != self.name:
            raise Refusal("this study needs its original Field Kit runtime; do not migrate active evidence")
        return state

    def save(self, state):
        records.write(self.directory / "study.json", state)

    def temper(self, path):
        return self.context.temper(locate(Path(path)))

    def initialize(self, document, temper=None):
        if self.directory.exists():
            raise Refusal("study already exists; inspect it or use a new study ID")
        self.directory.mkdir(parents=True)
        (self.directory / ".lock").touch()
        atomic_write(self.directory / "brief.md", document["brief"].encode())
        plan = {key: value for key, value in document.items() if key not in ("brief", "other_processes")}
        state = {**plan, "id": self.name, "state": "open", "approved_at": jobs.now(),
                 "deadline": time.time() + plan["budgets"]["seconds"], "runs": [], "removed": []}
        atomic_write(self.directory / "restore.md", machine.restore_text(self.name, state["user_layout"]))
        if state["user_layout"]["running"] and temper is not None:
            with exclusive_lock(self.directory / ".lock"):
                self.claim_machine(temper, state, initial=True)
        self.save(state)
        return state

    def claim_machine(self, temper, state, initial=False):
        try:
            if machine.claim(temper, state["user_layout"]):
                state["user_layout"]["stopped"] = True
            state["user_layout"].pop("stop_error", None)
        except Refusal as error:
            if not initial:
                raise
            # The approval stands; the next attempt stops the layout once its requests finish.
            state["user_layout"]["stop_error"] = str(error)

    def path_for(self, run):
        # Run identifiers are opaque timestamp names, not candidate identifiers.
        if not isinstance(run, str) or not run or Path(run).name != run or run in (".", "..") or "/" in run or "\\" in run:
            raise Refusal("invalid run path")
        path = self.directory / "runs" / run
        if path.is_symlink() or path.parent.is_symlink():
            raise Refusal("run directory must not be a symlink")
        return path

    def files(self, run):
        return records.AttemptFiles(self.path_for(run))

    def latest(self):
        runs = self.load()["runs"]
        return self.path_for(runs[-1]) if runs else None

    def runtime(self):
        """One Temper root per study; installations inside it follow software closures."""
        root = self.directory / ".runtime" / "temper"
        for path in (root, root.parent):
            if path.is_symlink():
                raise Refusal(f"owned runtime path was replaced with a symlink: {path}")
        return root

    def reserve(self, name, workload_path, reason, lock_path=None, temper_path=None, *, model_adapter=None):
        if not reason.strip():
            raise Refusal("name the question or observation motivating this trial")
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            if state["state"] != "open":
                raise Refusal("study is closed")
            if time.time() >= state["deadline"]:
                raise Refusal("approved time ended; return existing evidence or agree a scoped extension")
            self.require_idle(state)
            base = next((row for row in state["candidates"] if row["id"] == name), None)
            if base is None:
                raise Refusal("candidate is outside approved material")
            temper = self.temper(temper_path or state["temper"]["path"])
            chosen = candidate(temper, lock_path) if lock_path else base
            validate_variant(json.loads(base["lock"]), json.loads(chosen["lock"]), state["budgets"]["context_tokens"])
            facts = temper.machine_facts()
            for key in ("chip", "physical_memory_bytes", "target"):
                if facts.get(key) != state["machine"].get(key):
                    raise Refusal("machine differs from approved study")
            storage.require_cache(state["weights"]["cache"], state["weights_cache_explicit"])
            lock = json.loads(chosen["lock"])
            work = workloads.load(workload_path, model_adapter=model_adapter,
                                  interface=interfaces.resolve(lockview.interface(lock)))
            self.claim_machine(temper, state)
            self.save(state)
            run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            files = self.files(run_id)
            files.directory.mkdir(parents=True)
            atomic_write(files.lock, chosen["lock"].encode())
            atomic_write(files.workload, canonical_json(work))
            files.save_run(records.new_run(run_id=run_id, study=self.name, candidate=name, reason=reason,
                created_epoch=time.time(), created_at=jobs.now(), temper=temper.identity(),
                field_kit=runtime_identity(), machine=facts, resource_policy=state["resource_policy"],
                family=chosen["family"], execution=chosen["execution"],
                engine_adapter=engines.from_lock(lock).identifier, model_adapter=work["model_adapter"],
                model_adapter_scope="external-harness" if "command" in work else "request-workload",
                installation=lockview.installation(lock), weights_cache=state["weights"]["cache"],
                lock_sha256=chosen["lock_sha256"], workload_sha256=digest(canonical_json(work))))
            state["runs"].append(run_id)
            self.save(state)
            return files.directory

    def require_idle(self, state=None):
        for run in (state or self.load())["runs"]:
            row = self.files(run).load_run()
            if row["state"] not in records.TERMINAL:
                raise Refusal(f"run {run} is active or needs recovery; use study status/wait/recover")
            if not row.get("safe_to_cleanup"):
                raise Refusal(f"run {run} has uncertain process shutdown; use study recover")

    def recover(self):
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            recovered = []
            for run_id in state["runs"]:
                files = self.files(run_id)
                run = files.load_run()
                if run["state"] in records.TERMINAL and run.get("safe_to_cleanup"):
                    continue
                if jobs.alive(run.get("worker")):
                    raise Refusal("worker is still running; use study stop and wait for shutdown")
                if run["state"] == "queued" and not run.get("worker") and time.time() - run["created_epoch"] < 10:
                    raise Refusal("worker is starting; wait for startup before recovery")
                recovery.require_shutdown(files)
                run.update(state="interrupted", error=run.get("error") or "worker ended before committing its result",
                           safe_to_cleanup=True, recovered_at=jobs.now())
                files.save_run(run)
                records.write(files.result, run)
                recovered.append(run_id)
            return {"recovered": recovered, "next": "review retained evidence; a retry is a new trial"}

    def close(self):
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            self.require_idle(state)
            root = self.runtime()
            latest = {}
            for run_id in state["runs"]:
                files = self.files(run_id)
                record = files.load_run()
                latest[record["installation"]] = (files, record)
            for name, (files, record) in latest.items():
                if name in state["removed"]:
                    continue
                if root.exists():
                    self.temper(record["temper"]["path"]).remove(files.lock, root, name)
                state["removed"].append(name)
                self.save(state)
            runtime = self.directory / ".runtime"
            if runtime.is_symlink():
                raise Refusal("owned runtime was replaced with a symlink")
            if runtime.exists():
                shutil.rmtree(runtime)
            weights = self.close_weights(state)
            layout = self.restore_machine(state)
            state.update(state="closed", closed_at=jobs.now())
            self.save(state)
            return {"state": "closed", "evidence": str(self.directory), "weights": weights, "user_layout": layout}

    def close_weights(self, state):
        weights = state["weights"]
        if weights["cleanup"] == "keep":
            return "kept: " + weights["cache"]
        if "removal" not in weights:
            if not Path(weights["cache"]).is_dir():
                return "weights cache unavailable; connect it and close again to remove study-added files"
            weights["removal"] = storage.remove_added(weights["cache"], weights["files"])
            self.save(state)
        return weights["removal"]

    def restore_machine(self, state):
        layout = state["user_layout"]
        if not layout.get("stopped"):
            return "not stopped by this study"
        if not layout.get("restored"):
            try:
                machine.restore(self.temper(state["temper"]["path"]), layout)
            except (CommandFailure, Refusal) as error:
                layout["restore_error"] = str(error)
                self.save(state)
                return f"restore failed ({error}); run: {machine.restore_command(layout)}"
            layout.update(restored=True, restored_at=jobs.now())
            layout.pop("restore_error", None)
            self.save(state)
        return "restored: " + machine.restore_command(layout)

    def repair(self, report, patch=None):
        with exclusive_lock(self.directory / ".lock"):
            text = read_regular(report).decode()
            if not text.strip():
                raise Refusal("repair report is empty")
            name = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            folder = self.directory / "repairs" / name
            folder.mkdir(parents=True)
            atomic_write(folder / "report.md", text.encode())
            if patch:
                atomic_write(folder / "repair.diff", read_regular(patch, 16 * 1024**2))
            return {"report": str(folder / "report.md"), "included_in_export": True}
