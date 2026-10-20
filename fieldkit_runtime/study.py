"""One bounded, agent-directed Qwen study with reusable, study-owned material.

The agent chooses questions and interprets evidence. This module owns consent,
budgets, exact candidate scope, serial execution and recoverable cleanup.
"""
from __future__ import annotations

import copy
import datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any

from . import review
from .check import BasicCheck, GIB, INSTALLATION, UNFINISHED, load_protocol, result_document, render_report, utc_now
from .common import IDENTITY, Refusal, canonical_json, digest, load_json
from .process import atomic_write, exclusive_lock, file_digest, runtime_identity
from .temper import Temper, locate

PLAN_SCHEMA = "field-kit-qwen-study-plan/v1"
STUDY_SCHEMA = "field-kit-qwen-study/v1"
PURPOSES = ("screen", "tune", "diagnose", "confirm")


def opportunities(facts: dict[str, Any]) -> list[str]:
    large = facts["physical_memory_bytes"] > 32 * GIB
    return (["Prioritize Rapid MLX and vLLM Metal Qwen candidates that pass Temper's memory/admission plan;"
             " their larger memory needs left gaps on the reference M5 / 32 GiB.",
             "Bracket usable input plus output/continuation on this memory tier; a configured window is not evidence of fit."]
            if large else ["Measure gaps for this chip and memory tier; reopen a memory refusal only when material, settings, implementation or evidence changes."]) + [
        "Keep a cheap current Qwen baseline. Investigate material mismatches with expectations across quality, speed, memory, context and behavior, including the Q4/Q5 reversal and unexpected gains.",
        "Plan useful extensions after healthy passes: seek more usable context/output room and effective acceleration within approval, not just the catalog default.",
        "Skip already answered questions at the same engine, weights, template, settings and hardware identity.",
        "Treat missing counters, broken evaluation and unsupported interfaces as missing evidence, not bad model quality."]


def _identity(value: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise Refusal(f"invalid study or candidate ID: {value!r}")
    return value


def _owned_remove(path: Path) -> None:
    if path.is_symlink():
        raise Refusal(f"refusing cleanup through a symlink: {path}")
    if path.exists():
        shutil.rmtree(path)


def frozen_material(lock: dict[str, Any]) -> dict[str, Any]:
    """Exclude only the tuning controls explicitly covered by study consent.

    This reads Temper's published lock contract; it never constructs a lock.
    Software, artifacts, template, engine and all other fields stay exact.
    """
    result = copy.deepcopy(lock)
    selected = result["records"]["presets"].pop(result["preset"])
    result["preset"] = "selected"
    selected.pop("presentation", None)
    selected.pop("request_defaults", None)
    selected.pop("speculation", None)
    selected.pop("compute", None)
    for profile in selected["profiles"]:
        profile.pop("context_window_tokens", None)
        profile.pop("engine_config", None)
    result["records"]["presets"]["selected"] = selected
    result["records"].pop("date", None)
    result["records"].pop("presentation", None)
    return result


def validate_variant(base: dict[str, Any], variant: dict[str, Any], maximum_context: int) -> None:
    if frozen_material(base) != frozen_material(variant):
        raise Refusal("variant changes approved engine/software/weights/template or other fixed material; it needs a new study plan")
    selected = variant["records"]["presets"][variant["preset"]]
    for profile in selected["profiles"]:
        if not 0 < profile["context_window_tokens"] <= maximum_context:
            raise Refusal("variant exceeds the approved context ceiling")
    output = selected.get("request_defaults", {}).get("max_output_tokens", 0)
    if output > maximum_context:
        raise Refusal("variant exceeds the approved output ceiling")


def plan(repository: Path, temper: Temper, *, presets: list[str], files: list[Path], python: Path,
         max_runs: int, hours: float, download_gib: float, max_context: int, request_seconds: int,
         quality_turns: int = 8, quality_seconds: int = 14400, say=print) -> dict[str, Any]:
    if not presets and not files:
        raise Refusal("name the Qwen baselines and candidate preset files in the study plan")
    if max_runs < 1 or not 0 < hours <= 168 or download_gib < 0 or max_context < 512 or request_seconds < 1:
        raise Refusal("study budgets must be positive (downloads may be zero), with at most one week per approval")
    if type(quality_turns) is not int or quality_turns < 1 or type(quality_seconds) is not int or quality_seconds < 1:
        raise Refusal("coding quality response and time budgets must be positive integers")
    evaluator = review.evaluator(python)
    candidates = []
    for preset, source in [*((item, None) for item in presets), *((None, item) for item in files)]:
        say(f"Planning {preset or source} ...")
        check = BasicCheck(repository, temper, say=lambda _: None, require_complete_cache=True)
        check.start(preset, preset_path=source, plan_only=True)
        candidate = check.planned
        if candidate is None:
            raise Refusal("candidate did not produce a plan")
        candidate["id"] = _identity(candidate["run"]["preset"]["id"])
        lock = json.loads(candidate["lock"])
        validate_variant(lock, lock, max_context)
        candidates.append(candidate)
    ids = [candidate["id"] for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise Refusal("candidate IDs must be unique")
    # Conservative: shared weight files across different candidates may be counted twice.
    needed = sum(candidate["run"]["plan"]["remaining_download_bytes"] for candidate in candidates)
    if needed > download_gib * GIB:
        raise Refusal("candidate downloads exceed the study budget; narrow the shortlist or increase the proposed budget")
    disk = sum(candidate["run"]["plan"]["remaining_disk_bytes"] for candidate in candidates)
    free = min(candidate["run"]["plan"]["free_disk_bytes"] for candidate in candidates)
    if disk + 2 * GIB > free:
        raise Refusal("combined retained candidates exceed free disk with a 2 GiB working reserve; narrow the shortlist")
    first = candidates[0]["run"]
    return {"schema": PLAN_SCHEMA, "created_at": utc_now(), "machine": first["machine"],
            "temper": first["temper"], "field_kit": first["field_kit"], "evaluator": evaluator,
            "budgets": {"runs": max_runs, "seconds": int(hours * 3600), "download_bytes": int(download_gib * GIB),
                        "context_tokens": max_context, "request_seconds": request_seconds,
                        "quality_turns": quality_turns, "quality_seconds": quality_seconds},
            "disk": {"candidate_bytes": disk, "free_bytes": free, "working_reserve_bytes": 2 * GIB},
            "tuning_scope": "context, output allowance, engine_config, sampling/reasoning, speculation using approved artifacts, compute",
            "candidates": candidates}


def describe(document: dict[str, Any]) -> str:
    limits = document["budgets"]
    lines = [f"Qwen study on {document['machine'].get('chip', 'Apple Silicon')} / "
             f"{document['machine']['physical_memory_bytes'] / GIB:g} GiB",
             f"At most {limits['runs']} runs, {limits['seconds'] / 3600:g} hours from approval, "
             f"{limits['download_bytes'] / GIB:g} GiB downloads, {limits['context_tokens']:,} context tokens; "
             f"{limits['request_seconds']} seconds per request.",
             f"Retained candidate disk estimate: {document['disk']['candidate_bytes'] / GIB:.2f} GiB, plus 2 GiB working reserve; "
             f"{document['disk']['free_bytes'] / GIB:.2f} GiB free at planning.",
             "Candidates (exact engine/software, weights and template are frozen):"]
    for candidate in document["candidates"]:
        run = candidate["run"]
        lines.append(f"- {candidate['id']} / {run['preset']['family']}: "
                     f"up to {run['plan']['remaining_download_bytes'] / GIB:.2f} GiB downloads; lock {run['lock_sha256']}")
    lines += [f"Agent may tune: {document['tuning_scope']}.",
              "Smoke → coding quality assessment → one-shot performance and context/cache tuning → quality confirmation when needed.",
              f"Quality cases allow at most {limits['quality_turns']} responses and {limits['quality_seconds']} seconds including tests and repairs, within the total study budget.",
              "Performance coding is one response without repairs; review checks degradation against the qualified baseline.",
              "Plan useful extensions after healthy passes; reserve time for diagnosis and final confirmation.",
              "Investigate material surprises, including quant quality reversals; check the setup, measurement and expectation before generalizing.",
              "These budgets bound exploration, not preset capability. An untested upper boundary remains open when the budget ends.",
              f"Coding evaluator: {document['evaluator']['path']}",
              "Processes stop after each trial. Study-owned weights and software are reused until study close.",
              "Close removes owned installations, model caches and reconstructible source copies; results, patches, reviews and failures stay.",
              "Existing user caches are reused offline only when all model/template files are present; never deleted. Nothing is uploaded."]
    return "\n".join(lines)


class Study:
    def __init__(self, repository: Path, temper: Temper, identity: str):
        self.repository, self.temper = repository.resolve(), temper
        self.identity = _identity(identity)
        self.directory = self.repository / "studies" / self.identity
        if self.directory.is_symlink() or self.directory.parent.is_symlink():
            raise Refusal("study directory must not be a symlink")

    def load(self):
        state = load_json(self.directory / "study.json", 64 * 1024**2)
        if state.get("schema") != STUDY_SCHEMA or state.get("id") != self.identity:
            raise Refusal("unknown study state")
        return state

    def save(self, state):
        atomic_write(self.directory / "study.json", canonical_json(state))

    def runtime(self, candidate):
        root = self.directory / "runtime" / _identity(candidate)
        if root.is_symlink() or root.parent.is_symlink():
            raise Refusal("study runtime path must not be a symlink")
        return root

    def start(self, document: dict[str, Any], *, assume_yes=False, ask=input):
        if document.get("schema") != PLAN_SCHEMA:
            raise Refusal("not a Qwen study plan")
        self.verify(document)
        for candidate in document["candidates"]:
            _identity(candidate["id"])
            if digest(candidate["lock"].encode()) != candidate["run"]["lock_sha256"]:
                raise Refusal("candidate lock differs from its plan")
            validate_variant(json.loads(candidate["lock"]), json.loads(candidate["lock"]), document["budgets"]["context_tokens"])
        if review.evaluator(Path(document["evaluator"]["path"])) != document["evaluator"]:
            raise Refusal("the evaluator changed since planning")
        print(describe(document))
        if not assume_yes and ask("\nApprove this bounded study? Type yes: ").strip().lower() != "yes":
            return False
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / ".lock").touch(exist_ok=False)
        state = {"schema": STUDY_SCHEMA, "id": self.identity, "state": "open", "plan": document,
                 "approved_at": utc_now(), "deadline": time.time() + document["budgets"]["seconds"],
                 "runs": [], "charged_candidates": [], "reserved_download_bytes": 0, "removed_candidates": []}
        # The durable approval precedes every installation or model download.
        self.save(state)
        return True

    def verify(self, plan):
        if self.temper.identity() != plan["temper"] or runtime_identity() != plan["field_kit"]:
            raise Refusal("Temper or Field Kit changed since planning; retain the old study and use its original runtime")
        facts = self.temper.machine_facts()
        for key in ("chip", "physical_memory_bytes", "wired_limit_mib", "target"):
            if facts.get(key) != plan["machine"].get(key):
                raise Refusal(f"machine {key} changed since planning")

    def _candidate(self, state, identity):
        candidate = next((row for row in state["plan"]["candidates"] if row["id"] == identity), None)
        if candidate is None:
            raise Refusal("candidate is outside the approved study")
        return candidate

    def gate(self, state, candidate, kind, purpose, coding_mode="performance"):
        qualified = smoked = False
        confirmed = False
        baseline = None
        for entry in state["runs"]:
            directory = self.repository / "runs" / entry["id"]
            run = load_json(directory / "run.json", 64 * 1024**2)
            if run["state"] != "complete":
                raise Refusal(f"run {run['id']} is unfinished or retained; resume it before another trial")
            if run["workload"]["kind"] == "coding":
                verdict = review.verdict(directory)
                if verdict is None:
                    raise Refusal(f"run {run['id']} needs tests and code review before another experiment")
                tests_path = directory / "review/tests.json"
                if not tests_path.exists() or load_json(tests_path, 16 * 1024**2).get("evaluator") != state["plan"]["evaluator"]:
                    raise Refusal(f"run {run['id']} was not graded with the study's approved evaluator")
                if entry["candidate"] == candidate["id"]:
                    if run["workload"].get("coding_mode") == "quality" and verdict == "pass" \
                            and all(row.get("measurement_valid") for row in run["cases"]):
                        qualified = True
                        baseline = {"run": run["id"], "lock_sha256": run["lock_sha256"]}
                    confirmed |= run["purpose"] == "confirm"
            if entry["candidate"] == candidate["id"] and run["workload"]["kind"] == "smoke":
                smoked |= bool(run["cases"]) and all(row["status"] == "passed" and row.get("measurement_valid") for row in run["cases"])
        if kind != "smoke" and not smoked:
            raise Refusal("candidate needs a valid smoke pass first")
        if (kind in ("context", "cache") or purpose in ("tune", "confirm")) and not qualified and purpose != "diagnose":
            raise Refusal("candidate needs reviewed baseline quality before tuning or confirmation; use diagnose for a known failure")
        if confirmed and purpose != "diagnose":
            raise Refusal("this candidate has consumed its held-out case; further learning is diagnostic, not fresh confirmation")
        if coding_mode == "quality" and purpose == "tune":
            raise Refusal("performance tuning uses one-shot coding; full quality runs are assessment or diagnosis")
        if kind == "coding" and purpose == "confirm" and coding_mode != "quality":
            raise Refusal("coding quality confirmation requires --coding-mode quality")
        return baseline

    def run(self, candidate_id: str, *, kind="smoke", purpose="screen", variant: Path | None = None,
            records=256, output_tokens=512, coding_mode="performance", reason: str):
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            if state["state"] != "open":
                raise Refusal("study is not open")
            self.verify(state["plan"])
            if not reason.strip():
                raise Refusal("name the evidence gap or prior result that makes this trial useful")
            if purpose not in PURPOSES:
                raise Refusal("unknown trial purpose")
            candidate = self._candidate(state, candidate_id)
            if kind == "coding" and review.evaluator(Path(state["plan"]["evaluator"]["path"])) != state["plan"]["evaluator"]:
                raise Refusal("the approved grading environment changed")
            quality_baseline = self.gate(state, candidate, kind, purpose, coding_mode)
            limits = state["plan"]["budgets"]
            if len(state["runs"]) >= limits["runs"] or time.time() >= state["deadline"]:
                raise Refusal("study budget exhausted; close it or propose a new bounded study")
            if output_tokens > limits["context_tokens"]:
                raise Refusal("request output allowance exceeds the study ceiling")
            # Coding uses one tuning case; the second is consumed only for confirmation.
            case_id = ("template-decorators" if purpose == "confirm" else "async-stream") if kind == "coding" else None
            worker = StudyCheck(self, state, kind, case_id, records, output_tokens, coding_mode)
            if worker.unfinished():
                raise Refusal("another run is unfinished; finish or recover it before this study")
            selected = candidate
            if variant:
                planner = BasicCheck(self.repository, self.temper)
                planner.start(None, preset_path=variant, plan_only=True)
                selected = planner.planned
                validate_variant(json.loads(candidate["lock"]), json.loads(selected["lock"]), limits["context_tokens"])
            run = copy.deepcopy(selected["run"])
            run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            directory = self.repository / "runs" / run_id
            directory.mkdir(parents=True)
            (directory / ".lock").touch(exist_ok=False)
            atomic_write(directory / "execution.lock.json", selected["lock"].encode())
            runtime = self.runtime(candidate_id)
            for name, data in selected["files"].items():
                atomic_write(runtime / "presets.d" / name, data.encode())
            run.update(id=run_id, state="preparing", approved_at=state["approved_at"],
                       workload=worker.workload, case_ids=[case["id"] for case in worker.cases],
                       quality_evaluator=worker.quality_evaluator, quality_baseline=quality_baseline,
                       study={"id": self.identity, "candidate": candidate_id}, purpose=purpose, reason=reason,
                       lock_sha256=file_digest(directory / "execution.lock.json"), preparation=None, cases=[], cleanup="pending")
            run["limits"] = {**worker.protocol["limits"], "request_seconds": min(limits["request_seconds"], 300) if kind == "smoke" else limits["request_seconds"]}
            run["deadline"] = state["deadline"]
            # Material scope is frozen. Reusing downloads never grants permission
            # to touch the user's cache or a different candidate's installation.
            if candidate["run"]["cache"]["mode"] == "private":
                run["cache"] = {"mode": "private", "path": str(self.directory / "hf-cache")}
            else:
                run["cache"] = {"mode": "shared", "path": None}
            if candidate_id not in state["charged_candidates"]:
                charge = candidate["run"]["plan"]["remaining_download_bytes"]
                if state["reserved_download_bytes"] + charge > limits["download_bytes"]:
                    raise Refusal("study download budget exhausted")
                state["charged_candidates"].append(candidate_id)
                state["reserved_download_bytes"] += charge
            # Save intent before process effects. An interrupted reservation is
            # a visible unfinished run, never an invisible free retry.
            worker.save(directory, run)
            state["runs"].append({"id": run_id, "candidate": candidate_id})
            self.save(state)
            return worker.continue_run(directory)

    def resume(self):
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            self.verify(state["plan"])
            for entry in state["runs"]:
                directory = self.repository / "runs" / entry["id"]
                run = load_json(directory / "run.json", 64 * 1024**2)
                if run["state"] in UNFINISHED or run["state"] == "retained":
                    spec = run["workload"]
                    worker = StudyCheck(self, state, spec["kind"], spec["case_id"], spec["records"], spec["output_tokens"],
                                        spec.get("coding_mode", "performance"))
                    return worker.resume(directory)
            raise Refusal("study has no unfinished run")

    def close(self):
        with exclusive_lock(self.directory / ".lock"):
            state = self.load()
            if state["state"] == "closed":
                return
            # Cleanup does not need unchanged hardware/evaluator, but it does
            # need the original Temper binary and a proven stopped runtime.
            if self.temper.identity() != state["plan"]["temper"]:
                raise Refusal("cleanup needs the study's original Temper binary")
            latest = {}
            for entry in state["runs"]:
                directory = self.repository / "runs" / entry["id"]
                run = load_json(directory / "run.json", 64 * 1024**2)
                recorded = {row["id"] for row in run["cases"]}
                uncommitted = any(path.name not in recorded for path in (directory / "cases").glob("*"))
                if not run["safe_to_cleanup"] or run["state"] == "retained" or uncommitted:
                    raise Refusal(f"run {run['id']} has unfinished work or uncertain shutdown; retain recovery material")
                if run["state"] != "complete":
                    run.update(state="cancelled", stopped="study closed before completion")
                    atomic_write(directory / "run.json", canonical_json(run))
                    atomic_write(directory / "result.json", canonical_json(result_document(run)))
                    atomic_write(directory / "report.md", render_report(run, load_protocol(self.repository)[0]))
                latest[entry["candidate"]] = directory
            state["state"] = "closing"
            self.save(state)
            for candidate, directory in latest.items():
                if candidate in state["removed_candidates"]:
                    continue
                self.temper.remove(directory / "execution.lock.json", self.runtime(candidate), INSTALLATION)
                state["removed_candidates"].append(candidate)
                self.save(state)
            _owned_remove(self.directory / "runtime")
            _owned_remove(self.directory / "hf-cache")
            for entry in state["runs"]:
                directory = self.repository / "runs" / entry["id"]
                _owned_remove(directory / "fixture")
                review.prune(directory)
            state.update(state="closed", closed_at=utc_now())
            self.save(state)

    def report(self):
        state = self.load()
        limits = state["plan"]["budgets"]
        lines = [f"# Qwen study {self.identity}", "", f"State: {state['state']}",
                 f"Runs: {len(state['runs'])}/{limits['runs']}; download reservation: {state['reserved_download_bytes'] / GIB:.2f}"
                 f"/{limits['download_bytes'] / GIB:g} GiB; time remaining: {max(0, state['deadline'] - time.time()) / 3600:.2f} hours.",
                 "", describe(state["plan"]), "",
                 "| Run / purpose | Candidate | Work | Quality | Time (s) | Input / output tokens | Prefill / decode tok/s | Loaded / peak wired growth (GiB) |",
                 "|---|---|---|---|---:|---|---|---|"]
        for entry in state["runs"]:
            directory = self.repository / "runs" / entry["id"]
            run = load_json(directory / "run.json", 64 * 1024**2)
            try:
                quality = review.verdict(directory) if run["workload"]["kind"] == "coding" else None
            except Refusal:
                quality = "stale review; not qualified"
            for row in run["cases"]:
                memory = row.get("memory") or {}
                counters = [memory.get(key) for key in ("engine_loaded_wired_bytes", "engine_peak_wired_bytes")]
                mem = " / ".join("missing" if n is None else f"{n / GIB:.2f}" for n in counters)
                mode = f" / {run['workload'].get('coding_mode', 'performance')}" if run["workload"]["kind"] == "coding" else ""
                lines.append(f"| {entry['id']} / {run['purpose']} | {entry['candidate']} | {row['id']}{mode} | "
                             f"{quality or row['status']}{'' if row.get('measurement_valid') else ' (measurement invalid)'} | "
                             f"{row.get('task_seconds', 'missing')} | {row.get('input_tokens')} / {row.get('output_tokens')} | "
                             f"{row.get('prefill_tokens_per_second')} / {row.get('generation_tokens_per_second')} | {mem} |")
        lines += ["", "Trial decisions:"]
        for entry in state["runs"]:
            run = load_json(self.repository / "runs" / entry["id"] / "run.json", 64 * 1024**2)
            baseline = run.get("quality_baseline")
            lines.append(f"- {entry['id']}: {run.get('reason', 'not recorded')}" +
                         (f" Quality reference: {baseline['run']} / {baseline['lock_sha256']}." if baseline else ""))
        lines += ["", "Memory is sampled system wired growth over pre-load baseline, affected by other apps;"
                  " it is not a native allocation total or minimum required machine RAM. Missing counters stay missing.",
                  "None means missing, never zero. Context/cache table rates describe the last request; time covers the session."
                  " Steps retain their actual input/output counts and cache counters in each run."
                  " Output allowance is a ceiling, not proof that the model generated that many tokens.",
                  "Coding performance is one-shot; its review is a degradation check. Coding quality includes visible tests and repairs;"
                  " per-turn rates remain in the run, and peak wired growth may include the evaluator. Final independent grading and review are separate.",
                  "", "The agent's conclusion must name the selected run/lock, reviewed quality, hardware scope, measured"
                  " context boundary, unresolved anomalies and tradeoffs. A catalog change requires separate review."]
        text = "\n".join(lines) + "\n"
        atomic_write(self.directory / "report.md", text.encode())
        return text


class StudyCheck(BasicCheck):
    def __init__(self, study, state, kind, case_id, records, output_tokens, coding_mode="performance"):
        budgets = state["plan"]["budgets"]
        super().__init__(study.repository, study.temper, workload=kind, case_id=case_id, records=records, output_tokens=output_tokens,
                         coding_mode=coding_mode, quality_turns=budgets["quality_turns"], quality_seconds=budgets["quality_seconds"],
                         grading_python=Path(state["plan"]["evaluator"]["path"]))
        self.study = study
        self.deadline = state["deadline"]
        self.protocol["limits"]["request_seconds"] = min(self.protocol["limits"]["request_seconds"], state["plan"]["budgets"]["request_seconds"])

    def runtime_root(self, directory, run):
        return self.study.runtime(run["study"]["candidate"])

    def environment(self, run):
        environment = super().environment(run)
        if run["cache"]["mode"] == "shared":
            # If previously cached model bytes disappear, fail instead of
            # silently downloading large files into a user-owned cache.
            environment["HF_HUB_OFFLINE"] = "1"
        return environment

    def cleanup(self, directory, run):
        # Temper's final stopped proof has already been checked by measure().
        # Software and private weights remain reusable until Study.close().
        _owned_remove(directory / "fixture")
        run.update(state="complete", cleanup="study-owned material retained until close")


def command(arguments, repository: Path) -> int:
    if arguments.action == "run" and arguments.background:
        from . import background
        forwarded = ["run", arguments.id, "--candidate", arguments.candidate, "--workload", arguments.workload,
                     "--purpose", arguments.purpose, "--reason", arguments.reason, "--records", str(arguments.records),
                     "--output-tokens", str(arguments.output_tokens), "--coding-mode", arguments.coding_mode]
        if arguments.preset_file:
            forwarded += ["--preset-file", str(arguments.preset_file.resolve())]
        if arguments.temper:
            forwarded += ["--temper", str(arguments.temper.resolve())]
        return background.start(repository, forwarded, command="study")
    temper = Temper(locate(arguments.temper))
    action = arguments.action
    if action == "plan":
        document = plan(repository, temper, presets=arguments.preset, files=arguments.preset_file, python=arguments.python,
                        max_runs=arguments.max_runs, hours=arguments.hours, download_gib=arguments.download_gib,
                        max_context=arguments.max_context, request_seconds=arguments.request_seconds,
                        quality_turns=arguments.quality_turns, quality_seconds=arguments.quality_seconds)
        if arguments.out.exists():
            raise Refusal("plan output already exists; retain it and choose a new path")
        atomic_write(arguments.out, canonical_json(document))
        print(describe(document))
        print(f"Plan: {arguments.out}")
        return 0
    study = Study(repository, temper, arguments.id)
    if action == "start":
        if not study.start(load_json(arguments.plan, 64 * 1024**2), assume_yes=arguments.yes):
            return 0
    elif action == "run":
        return study.run(arguments.candidate, kind=arguments.workload, purpose=arguments.purpose,
                         variant=arguments.preset_file, records=arguments.records, output_tokens=arguments.output_tokens,
                         coding_mode=arguments.coding_mode, reason=arguments.reason)
    elif action == "resume":
        return study.resume()
    elif action == "close":
        study.close()
    print(study.report())
    return 0
