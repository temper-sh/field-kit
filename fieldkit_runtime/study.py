"""Study-owned material and attempt custody. Research policy stays with the agent."""
from __future__ import annotations
import datetime
import json
import os
from pathlib import Path
import shutil
import time
from . import jobs, workloads
from .common import IDENTITY, Refusal, canonical_json, digest, load_json, read_regular
from .material import validate_variant
from .measurements import POLICY
from .process import atomic_write, exclusive_lock, runtime_identity
from .temper import Temper, locate

SCHEMA = "field-kit-study/v2"
GIB = 1024**3

def identity(value):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise Refusal("use a plain lowercase study/candidate ID with dots or dashes")
    return value

def declared_material_bytes(locks):
    """Known model/template/software file sizes, deduplicated by content identity.
    This is a disclosure floor, never a runtime-memory or total-download estimate.
    """
    files = {}
    def visit(value):
        if isinstance(value, dict):
            sha = value.get("sha256")
            size = value.get("bytes", value.get("size"))
            if isinstance(sha, str) and type(size) is int and size >= 0:
                files[sha] = max(files.get(sha, 0), size)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for lock in locks:
        visit(lock["records"])
    return sum(files.values())

def candidate(temper, path):
    raw = read_regular(path, 16 * 1024**2)
    lock = json.loads(raw)
    execution = temper.inspect(path)
    name = identity(execution["preset"])
    selected = lock["records"]["presets"][name]
    family = lock["records"]["engines"][selected["engine"]]["adapter"].split("/")[0]
    if family not in ("llama-server", "splash", "rapid-mlx", "vllm-metal"):
        raise Refusal(f"no process-measurement support for {family}; see docs/REPAIR.md")
    if selected.get("interface") != "chat-completions":
        raise Refusal("this executor measures chat-completions candidates")
    return {"id": name, "lock": raw.decode(), "lock_sha256": digest(raw),
            "execution": execution, "family": family}

def preview(temper, locks, brief, *, hours=8, download_gib=100, max_context=262144,
            request_seconds=1800, trial_seconds=14400):
    if not locks or not 0 < hours <= 168 or download_gib < 0 or max_context < 512 or request_seconds < 1 or trial_seconds < 1:
        raise Refusal("name candidates and positive time/context limits (download allowance may be zero)")
    text = read_regular(brief).decode()
    if not text.strip():
        raise Refusal("write the study question and scope in the brief")
    candidates = [candidate(temper, path) for path in locks]
    if len({row["id"] for row in candidates}) != len(candidates):
        raise Refusal("candidate IDs must be distinct")
    documents = [json.loads(row["lock"]) for row in candidates]
    for document in documents:
        validate_variant(document, document, max_context)
    known = declared_material_bytes(documents)
    if known > download_gib * GIB:
        raise Refusal("declared material exceeds the proposed allowance; reuse/narrow candidates or review a larger scope")
    return {"schema": SCHEMA, "brief": text, "temper": temper.identity(), "machine": temper.machine_facts(),
            "resource_policy": POLICY,
            "budgets": {"seconds": int(hours * 3600), "download_bytes": int(download_gib * GIB),
                        "context_tokens": max_context, "request_seconds": request_seconds, "trial_seconds": trial_seconds},
            "known_material_bytes": known,
            "download_limit_scope": "Approved material allowance, not a network byte meter. Known file sizes exclude unlisted dependencies/retries. Review Temper's plan and unknowns before approval.",
            "cache": "study-owned HF cache; reused across trials and removed on close",
            "candidates": candidates}

def summary(document):
    candidates = []
    for row in document["candidates"]:
        records = json.loads(row["lock"])["records"]
        preset = records["presets"][row["id"]]
        candidates.append({"id": row["id"], "family": row["family"], "lock_sha256": row["lock_sha256"],
            "profiles": preset["profiles"], "request_defaults": preset.get("request_defaults"),
            "speculation": preset.get("speculation"),
            "artifacts": list(records.get("artifacts", {})), "templates": list(records.get("templates", {}))})
    return {"temper": document["temper"], "machine": document["machine"], "budgets": document["budgets"],
            "known_material_bytes": document["known_material_bytes"], "download_limit_scope": document["download_limit_scope"],
            "cache": document["cache"], "resource_policy": document["resource_policy"],
            "candidates": candidates, "brief": document["brief"]}

class Study:
    def __init__(self, repository, name):
        self.repository = Path(repository).resolve()
        self.name = identity(name)
        self.directory = self.repository / ".work" / "studies" / self.name
        if any(path.is_symlink() for path in (self.directory, self.directory.parent, self.directory.parent.parent)):
            raise Refusal("study directories must not be symlinks")

    def load(self):
        state = load_json(self.directory / "study.json", 64 * 1024**2)
        if state.get("schema") != SCHEMA or state.get("id") != self.name:
            raise Refusal("this study needs its original Field Kit runtime; do not migrate active evidence")
        return state

    def save(self, state):
        atomic_write(self.directory / "study.json", canonical_json(state))

    def initialize(self, document):
        if self.directory.exists():
            raise Refusal("study already exists; inspect it or use a new study ID")
        self.directory.mkdir(parents=True)
        (self.directory / ".lock").touch()
        atomic_write(self.directory / "brief.md", document["brief"].encode())
        plan = {key: value for key, value in document.items() if key != "brief"}
        state = {**plan, "id": self.name, "state": "open", "approved_at": jobs.now(),
                 "deadline": time.time() + plan["budgets"]["seconds"], "runs": [], "removed": []}
        self.save(state)
        return state

    def path_for(self, run):
        # Run identifiers are opaque timestamp names, not candidate identifiers.
        if not isinstance(run, str) or not run or Path(run).name != run or run in (".", "..") or "/" in run or "\\" in run:
            raise Refusal("invalid run path")
        path = self.directory / "runs" / run
        if path.is_symlink() or path.parent.is_symlink():
            raise Refusal("run directory must not be a symlink")
        return path

    def latest(self):
        runs = self.load()["runs"]
        return self.path_for(runs[-1]) if runs else None

    def runtime(self, name):
        root = self.directory / ".runtime" / "engines" / identity(name)
        for path in (root, root.parent, root.parent.parent):
            if path.is_symlink():
                raise Refusal(f"owned runtime path was replaced with a symlink: {path}")
        return root

    def reserve(self, name, workload_path, reason, lock_path=None, temper_path=None):
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
            temper = Temper(locate(temper_path or Path(state["temper"]["path"])))
            if lock_path:
                chosen = candidate(temper, lock_path)
            else:
                chosen = base
            validate_variant(json.loads(base["lock"]), json.loads(chosen["lock"]), state["budgets"]["context_tokens"])
            facts = temper.machine_facts()
            for key in ("chip", "physical_memory_bytes", "target"):
                if facts.get(key) != state["machine"].get(key):
                    raise Refusal("machine differs from approved study")
            work = workloads.load(workload_path)
            run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            directory = self.path_for(run_id)
            directory.mkdir(parents=True)
            for file, data in (("execution.lock.json", chosen["lock"].encode()), ("workload.json", canonical_json(work))):
                atomic_write(directory / file, data)
            run = {"id": run_id, "study": self.name, "candidate": name, "state": "queued", "reason": reason,
                   "created_epoch": time.time(), "created_at": jobs.now(), "worker": None,
                   "temper": temper.identity(), "field_kit": runtime_identity(), "machine": facts,
                   "resource_policy": state["resource_policy"],
                   "family": chosen["family"], "execution": chosen["execution"],
                   "lock_sha256": chosen["lock_sha256"], "workload_sha256": digest(canonical_json(work)),
                   "safe_to_cleanup": True, "sessions": [], "error": None}
            atomic_write(directory / "run.json", canonical_json(run))
            state["runs"].append(run_id)
            self.save(state)
            return directory

    def require_idle(self, state=None):
        for run in (state or self.load())["runs"]:
            directory = self.path_for(run)
            row = load_json(directory / "run.json", 64 * 1024**2)
            if row["state"] not in jobs.TERMINAL:
                raise Refusal(f"run {run} is active or needs recovery; use study status/wait/recover")
            if not row.get("safe_to_cleanup"):
                raise Refusal(f"run {run} has uncertain process shutdown; use study recover")

    def recover(self):
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            recovered = []
            for run_id in state["runs"]:
                directory = self.path_for(run_id)
                run = load_json(directory / "run.json", 64 * 1024**2)
                if run["state"] in jobs.TERMINAL and run.get("safe_to_cleanup"):
                    continue
                if jobs.alive(run.get("worker")):
                    raise Refusal("worker is still running; use study stop and wait for shutdown")
                if run["state"] == "queued" and not run.get("worker") and time.time() - run["created_epoch"] < 10:
                    raise Refusal("worker is starting; wait for startup before recovery")
                from .execution import recover_shutdown
                recover_shutdown(directory, run)
                run.update(state="interrupted", error=run.get("error") or "worker ended before committing its result",
                           safe_to_cleanup=True, recovered_at=jobs.now())
                atomic_write(directory / "run.json", canonical_json(run))
                atomic_write(directory / "result.json", canonical_json(run))
                recovered.append(run_id)
            return {"recovered": recovered, "next": "review retained evidence; a retry is a new trial"}

    def close(self):
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            self.require_idle(state)
            for row in state["candidates"]:
                name = row["id"]
                if name in state["removed"]:
                    continue
                root = self.runtime(name)
                matching = [self.path_for(run) for run in state["runs"]
                            if load_json(self.path_for(run) / "run.json")["candidate"] == name]
                if root.exists() and matching:
                    latest = matching[-1]
                    record = load_json(latest / "run.json", 64 * 1024**2)
                    temper = Temper(locate(Path(record["temper"]["path"])))
                    temper.remove(latest / "execution.lock.json", root, "field-kit")
                state["removed"].append(name)
                self.save(state)
            runtime = self.directory / ".runtime"
            if runtime.is_symlink():
                raise Refusal("owned runtime was replaced with a symlink")
            if runtime.exists():
                shutil.rmtree(runtime)
            state.update(state="closed", closed_at=jobs.now())
            self.save(state)
            return {"state": "closed", "evidence": str(self.directory)}

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
