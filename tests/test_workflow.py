from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from fieldkit_runtime.actions import propose_action
from fieldkit_runtime.artifacts import (
    ARTIFACT_SCHEMA,
    MATERIAL_SCHEMA,
    STEP_RESULT_SCHEMA,
    build_step,
)
from fieldkit_runtime.catalog import canonical_json, digest, parse_machine_facts
from fieldkit_runtime.planner import planned_paths
from fieldkit_runtime.workflow import (
    CommandResult,
    Workflow,
    _exclusive_session_lock,
    build_export,
    load_session,
    run_process_silent,
)
from tests.fixtures import adaptive_question_entry, question_entry
from tests.test_catalog import ROOT, facts_bytes


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail_contains = ""
        self.preparation_terminal = False
        self.artifact_padding = 0
        self.next_actions = None
        self.next_action_batches: list[object] = []
        self.step_stdout_padding = 0
        self.dynamic_generation = False
        self.protocol_answer = {
            "state": "observed",
            "value": {"completed": True},
        }

    def __call__(self, arguments, _timeout_seconds: float) -> CommandResult:
        argv = list(arguments)
        self.calls.append(argv)
        joined = " ".join(argv)
        if self.fail_contains and self.fail_contains in f" {joined} ":
            return CommandResult(b"partial output\n", b"injected failure\n", 7)
        if argv[-1:] == ["version"] and Path(argv[0]).name == "temper":
            return CommandResult(b"temper 0.1.0-alpha.4\n", b"", 0)
        if argv[1:3] == ["execution", "export"]:
            lock = Path(argv[argv.index("--lock") + 1])
            destination = Path(argv[argv.index("--out") + 1])
            dry = "--dry-run" in argv
            files = {name: (name + "\n").encode() for name in ("manifest.yaml", "manifest.lock.yaml", "software.lock.yaml", "request-defaults.json")}
            if not dry:
                destination.mkdir(exist_ok=True)
                for name, data in files.items():
                    (destination / name).write_bytes(data)
            return CommandResult(canonical_json({
                "schema": "temper-execution-inputs/v1", "profile": "fixture", "layouts": ["fixture-layout"],
                "lock_sha256": digest(lock.read_bytes()), "execution_digest": "a" * 64,
                "changed": not dry, "dry_run": dry,
                "inputs": {name: {"path": str(destination / name), "sha256": digest(data)} for name, data in files.items()},
            }), b"", 0)
        if " software install " in f" {joined} ":
            return CommandResult(b"RESULT software-install changed installation=field-kit-fixture packages=2 units=2 effects=2 claims=0\n", b"", 0)
        if " fetch " in f" {joined} ":
            return CommandResult(b"RESULT fetch changed layout=fixture-layout artifacts=2\n", b"", 0)
        if " apply " in f" {joined} ":
            return CommandResult(("RESULT apply changed mode=fixture generation=" + "b" * 64 + "\n").encode(), b"", 0)
        if " software check " in f" {joined} ":
            return CommandResult(b"RESULT software-check exact installation=field-kit-fixture packages=2 units=2 requirements=0 problems=0 receipt=abc\n", b"", 0)
        if " check " in f" {joined} ":
            return CommandResult(b"RESULT check ok mode=fixture verification=full layouts=1 problems=0\n", b"", 0)
        if " field-kit bind " in f" {joined} ":
            return CommandResult(b"schema: temper-field-kit-binding/v1\nfixture: true\n", b"", 0)
        if "fixture-protocol.py" in joined:
            report_path = Path(argv[argv.index("--report") + 1])
            action_path = Path(argv[argv.index("--action") + 1])
            step_path = Path(argv[argv.index("--step") + 1])
            step = json.loads(step_path.read_bytes())
            produced_artifacts = {}
            if step["step"]["kind"] == "preparation" and not self.preparation_terminal:
                artifact_name = next(iter(step["outputs"]))
                artifact_root = Path(step["outputs"][artifact_name]["root"])
                artifact_root.mkdir(parents=True)
                manifest_data = canonical_json({
                    "schema": "field-kit-fixture-prepared/v1",
                    "parameters": step["action"]["parameters"],
                })
                manifest_path = artifact_root / "manifest.json"
                manifest_path.write_bytes(manifest_data)
                producer = {
                    "action_sha256": digest(action_path.read_bytes()),
                    "step_sha256": digest(step_path.read_bytes()),
                }
                files = [{
                    "path": "manifest.json",
                    "bytes": len(manifest_data),
                    "sha256": digest(manifest_data),
                }]
                if self.dynamic_generation:
                    bound_files = {
                        "binding.yaml": b"schema: temper-field-kit-binding/v1\n",
                        "dynamic-manifest.yaml": b"schema: temper-manifest/v1\n",
                        "dynamic.lock.yaml": b"schema: temper-lock/v1\n",
                    }
                    material = {
                        "schema": MATERIAL_SCHEMA,
                        "model": "fixture-layout",
                        "generation": "c" * 64,
                        "manifest": {
                            "path": "dynamic-manifest.yaml",
                            "sha256": digest(bound_files["dynamic-manifest.yaml"]),
                        },
                        "manifest_lock": {
                            "path": "dynamic.lock.yaml",
                            "sha256": digest(bound_files["dynamic.lock.yaml"]),
                        },
                        "temper_binding": {
                            "path": "binding.yaml",
                            "sha256": digest(bound_files["binding.yaml"]),
                        },
                    }
                    bound_files["material.json"] = canonical_json(material)
                    for name, data in bound_files.items():
                        (artifact_root / name).write_bytes(data)
                        files.append({
                            "path": name,
                            "bytes": len(data),
                            "sha256": digest(data),
                        })
                if self.artifact_padding:
                    padding = b"x" * self.artifact_padding
                    (artifact_root / "padding.bin").write_bytes(padding)
                    files.append({
                        "path": "padding.bin",
                        "bytes": len(padding),
                        "sha256": digest(padding),
                    })
                files.sort(key=lambda item: item["path"])
                identity = {
                    "schema": ARTIFACT_SCHEMA,
                    "producer": producer,
                    "files": files,
                }
                produced_artifacts[artifact_name] = {
                    **identity,
                    "id": digest(canonical_json(identity)),
                }
            consumed_artifacts = {
                name: item["artifact"]["id"]
                for name, item in step["inputs"].items()
            }
            terminal = (
                step["step"]["kind"] == "measurement"
                or self.preparation_terminal
            )
            next_actions = self.next_actions
            if terminal and self.next_action_batches:
                next_actions = self.next_action_batches.pop(0)
            report = {
                "schema": STEP_RESULT_SCHEMA,
                "status": "complete",
                "action_sha256": digest(action_path.read_bytes()),
                "step_sha256": digest(step_path.read_bytes()),
                "outcome": "terminal" if terminal else "continue",
                "consumed_artifacts": consumed_artifacts,
                "produced_artifacts": produced_artifacts,
                "answers": (
                    {"workflow": {
                        "state": "unknown",
                        "reason": "synthetic preparation ended the target point",
                    }}
                    if self.preparation_terminal and step["step"]["kind"] == "preparation"
                    else {"workflow": self.protocol_answer} if terminal else None
                ),
                "protocol": {
                    "schema": "field-kit-fixture-protocol/v1",
                    "status": "complete",
                    "model": "fixture-layout",
                    "generation": (
                        "c" * 64
                        if self.dynamic_generation and step["step"]["kind"] == "measurement"
                        else "b" * 64
                    ),
                    "checks": {"control": {"exact": True}},
                    "resources": [{"swap_growth_mib": 0.0}],
                },
                "next_actions": next_actions if terminal else None,
            }
            report_path.write_bytes(canonical_json(report))
            return CommandResult(
                b'{"status":"complete"}\n' + b"x" * self.step_stdout_padding,
                b"",
                0,
            )
        if " software remove " in f" {joined} ":
            return CommandResult(b"RESULT software-remove changed installation=field-kit-fixture packages=2 units=2 effects=2 claims=0\n", b"", 0)
        raise AssertionError(f"unexpected command: {argv}")


class WorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.entry = question_entry()
        self.facts_data = facts_bytes()
        self.facts = parse_machine_facts(self.facts_data)

    def _temper(self, directory: Path) -> Path:
        temper = directory / "temper"
        temper.write_bytes(b"fake temper binary\n")
        temper.chmod(0o755)
        return temper

    def _start(self, workflow: Workflow, root: Path, outcome: str):
        plan = workflow.plan(root, outcome)
        session, session_path = workflow.start(plan, plan.sha256)
        return plan, session, session_path

    def test_silent_runner_retains_output_without_polluting_agent_stdout(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = run_process_silent(["/usr/bin/printf", "agent-safe"])
        self.assertEqual(result.stdout, b"agent-safe")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.getvalue(), "")

    def test_planning_is_stable_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            first = workflow.plan(root, "keep")
            second = workflow.plan(root, "keep")
            self.assertEqual(first.data, second.data)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.document["execution"]["paths"]["root"], str(root))
            self.assertEqual(
                first.document["investigation"]["initial_action"]["id"],
                "measure-fixture",
            )
            self.assertTrue(all(not path.exists() for path in planned_paths(root).values()))

    def test_start_requires_the_exact_approved_plan_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            plan = workflow.plan(root, "keep")
            with self.assertRaisesRegex(ValueError, "approval does not match"):
                workflow.start(plan, "0" * 64)
            self.assertTrue(all(not path.exists() for path in planned_paths(root).values()))

    def test_interrupted_execution_export_rolls_back_only_new_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            unrelated = workspace / "unrelated"
            unrelated.write_bytes(b"preserve")
            base = FakeRunner()
            def interrupted(arguments, timeout):
                if list(arguments)[1:3] == ["execution", "export"]:
                    raise KeyboardInterrupt("injected export interruption")
                return base(arguments, timeout)
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), interrupted)
            with self.assertRaises(KeyboardInterrupt):
                self._start(workflow, root, "restore")
            self.assertTrue(all(not path.exists() for path in planned_paths(root).values()))
            self.assertEqual(unrelated.read_bytes(), b"preserve")

    def test_unconfirmed_process_shutdown_prevents_installation_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            base = FakeRunner()
            def uncertain(arguments, timeout):
                result = base(arguments, timeout)
                if "--report" in arguments:
                    path = Path(arguments[arguments.index("--report") + 1])
                    report = json.loads(path.read_bytes())
                    report["protocol"]["safe_to_cleanup"] = False
                    path.write_bytes(canonical_json(report))
                return result
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), uncertain)
            _, _, session_path = self._start(workflow, root, "restore")
            with self.assertRaisesRegex(ValueError, "shutdown was not established"):
                workflow.run(session_path, lambda: True)
            self.assertTrue(root.exists())
            self.assertFalse(any(call[1:3] == ["software", "remove"] for call in base.calls))

    def test_keep_run_retains_complete_external_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), runner)
            plan, _, session_path = self._start(workflow, root, "keep")
            completed = workflow.run(session_path, lambda: self.fail("keep must not ask for restore"))
            self.assertEqual(completed["state"], "complete")
            self.assertEqual(completed["cleanup"], "not-requested")
            self.assertTrue(root.is_dir())
            self.assertEqual(completed["plan"]["sha256"], plan.sha256)
            self.assertTrue(Path(completed["paths"]["plan"]).is_file())
            self.assertTrue(Path(completed["paths"]["report"]).is_file())
            self.assertEqual(len([stage for stage in completed["stages"] if stage["state"] == "complete"]), 7)
            self.assertEqual(len(completed["attempts"]), 8)
            self.assertTrue(all(attempt["state"] == "complete" for attempt in completed["attempts"]))
            measurement = [attempt for attempt in completed["attempts"] if attempt["kind"] == "question-action"]
            self.assertEqual(len(measurement), 1)
            self.assertEqual(measurement[0]["action"]["id"], "measure-fixture")
            self.assertTrue(Path(measurement[0]["action"]["path"]).is_file())
            self.assertEqual(completed["protocol_evidence"]["status"], "complete")
            self.assertEqual(completed["answers"]["workflow"]["state"], "observed")
            packet, summary = build_export(session_path)
            self.assertEqual(summary["protocol"], "retained")
            self.assertIn(b'"schema": "field-kit-evidence-export/v2"', packet)
            self.assertIn(plan.sha256.encode(), packet)
            exported = json.loads(packet)
            self.assertEqual(len(exported["action_evidence"]), 1)
            self.assertEqual(exported["action_evidence"][0]["state"], "complete")

    def test_adaptive_action_refuses_to_continue_after_uncertain_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            base = FakeRunner()
            def uncertain(arguments, timeout):
                result = base(arguments, timeout)
                if any("fixture-protocol.py" in str(value) for value in arguments):
                    path = Path(arguments[arguments.index("--report") + 1])
                    report = json.loads(path.read_bytes())
                    report["protocol"]["safe_to_cleanup"] = False
                    path.write_bytes(canonical_json(report))
                return result
            workflow = Workflow(adaptive_question_entry(), self.facts, self.facts_data, self._temper(workspace), uncertain)
            _, _, session_path = self._start(workflow, workspace / "run", "restore")
            session = workflow.run(session_path, lambda: True)
            self.assertEqual(session["state"], "awaiting-action")
            before = len(session["attempts"])
            with self.assertRaisesRegex(ValueError, "shutdown was not established"):
                workflow.submit_action(session_path, {
                    "id": "final-context-validation", "parameters": {"target-context-tokens": 98304},
                    "reason": "must not run while process ownership remains unresolved",
                })
            self.assertEqual(len(load_session(session_path)["attempts"]), before)

    def test_restore_removes_only_root_and_retains_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), runner)
            _, _, session_path = self._start(workflow, root, "restore")
            confirmations = 0

            def confirm() -> bool:
                nonlocal confirmations
                confirmations += 1
                return True

            completed = workflow.run(session_path, confirm)
            self.assertEqual(confirmations, 1)
            self.assertFalse(root.exists())
            self.assertTrue(session_path.is_file())
            self.assertTrue(Path(completed["paths"]["evidence"]).is_dir())
            self.assertTrue(Path(completed["paths"]["report"]).is_file())
            self.assertEqual(load_session(session_path)["cleanup"], "complete")

    def test_question_action_total_runtime_is_refused_before_an_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            entry = adaptive_question_entry()
            workflow = Workflow(entry, self.facts, self.facts_data, self._temper(workspace), runner)
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)
            workflow.run(session_path, lambda: False)
            session = load_session(session_path)
            session["action_elapsed_seconds"] = entry.package["investigation"][
                "total_runtime_minutes_max"
            ] * 60
            session_path.write_bytes(canonical_json(session))
            calls_before = len(runner.calls)

            with self.assertRaisesRegex(ValueError, "question-action time bound is exhausted"):
                workflow.submit_action(session_path, {
                    "id": "measure-context",
                    "parameters": {"target-context-tokens": 131072},
                    "reason": "should be refused before execution",
                })

            self.assertTrue(all("--dry-run" in call for call in runner.calls[calls_before:]))
            self.assertFalse(
                (Path(session["paths"]["evidence"]) / "actions" / "attempt-0008").exists()
            )

    def test_concurrent_session_advance_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            _, _, session_path = self._start(workflow, root, "keep")
            lock_path = Path(load_session(session_path)["paths"]["lock"])

            with _exclusive_session_lock(lock_path):
                with self.assertRaisesRegex(ValueError, "already running"):
                    workflow.run(session_path, lambda: False)

    def test_interrupted_question_action_is_charged_its_full_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            entry = adaptive_question_entry()
            workflow = Workflow(entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)
            workflow.run(session_path, lambda: False)
            session = load_session(session_path)
            before = session["action_elapsed_seconds"]
            action = propose_action(entry.package["investigation"], {
                "id": "measure-context",
                "parameters": {"target-context-tokens": 131072},
                "reason": "synthetic interrupted action",
            }, session["attempts"])
            attempt_id = "attempt-0008"
            action_root = Path(session["paths"]["evidence"]) / "actions" / attempt_id
            action_path = action_root / "action.json"
            action_path.parent.mkdir(parents=True)
            action_path.write_bytes(action.data)
            step_definition = next(
                item for item in entry.package["investigation"]["actions"]
                if item["id"] == action.document["id"]
            )["steps"][0]
            step = build_step(
                action=action.document,
                action_sha256=action.sha256,
                step=step_definition,
                step_index=1,
                plan_sha256=session["plan"]["sha256"],
                inputs={},
                artifact_root=action_root / "artifacts",
            )
            step_path = action_root / "steps" / step_definition["id"] / "step.json"
            step_path.parent.mkdir(parents=True)
            step_path.write_bytes(step.data)
            session["attempts"].append({
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
                "timeout_seconds": 120.0,
                "steps": [{
                    "id": step_definition["id"],
                    "index": 1,
                    "kind": step_definition["kind"],
                    "step": {"path": str(step_path), "sha256": step.sha256},
                    "arguments": ["synthetic"],
                    "state": "running",
                    "started_at": "2026-09-01T00:00:00Z",
                    "timeout_seconds": 60.0,
                }],
                "artifacts": {},
                "state": "running",
                "started_at": "2026-09-01T00:00:00Z",
            })
            session_path.write_bytes(canonical_json(session))

            resumed = workflow.resume(session_path)

            self.assertEqual(resumed["attempts"][-1]["state"], "interrupted")
            self.assertEqual(resumed["attempts"][-1]["steps"][-1]["state"], "interrupted")
            self.assertEqual(resumed["attempts"][-1]["steps"][-1]["elapsed_seconds"], 60.0)
            self.assertAlmostEqual(resumed["action_elapsed_seconds"], before + 60.0, places=5)

    def test_resume_refuses_tampered_completed_action_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            _, _, session_path = self._start(workflow, root, "keep")
            completed = workflow.run(session_path, lambda: False)
            action_attempt = next(
                attempt for attempt in completed["attempts"]
                if attempt["kind"] == "question-action"
            )
            report_path = Path(action_attempt["protocol_report"]["path"])
            report_path.write_bytes(report_path.read_bytes() + b"\n")

            with self.assertRaisesRegex(ValueError, "report hash differs"):
                workflow.resume(session_path)

    def test_valid_negative_answer_completes_without_becoming_an_operational_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            runner.protocol_answer = {
                "state": "failed",
                "reason": "synthetic oracle mismatch",
                "value": {"completed": False},
            }
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), runner)
            _, _, session_path = self._start(workflow, root, "keep")
            completed = workflow.run(session_path, lambda: False)
            self.assertEqual(completed["state"], "complete")
            self.assertEqual(completed["answers"]["workflow"]["state"], "failed")
            report = Path(completed["paths"]["report"]).read_text()
            self.assertIn("`workflow`: **failed**", report)
            self.assertIn("synthetic oracle mismatch", report)

    def test_adaptive_question_records_multiple_targets_and_requires_final_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            entry = adaptive_question_entry()
            workflow = Workflow(entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)

            awaiting = workflow.run(session_path, lambda: False)
            self.assertEqual(awaiting["state"], "awaiting-action")
            self.assertEqual(
                awaiting["action_results"][0]["answers"]["workflow"]["state"],
                "observed",
            )
            first_action = [
                attempt for attempt in awaiting["attempts"]
                if attempt["kind"] == "question-action"
            ][0]
            self.assertEqual(first_action["candidate"]["target-context-tokens"], 98304)
            self.assertEqual(
                [step["kind"] for step in first_action["steps"]],
                ["preparation", "measurement"],
            )
            prepared = first_action["artifacts"]["prepared-context"]
            measurement_step = json.loads(
                Path(first_action["steps"][1]["step"]["path"]).read_bytes()
            )
            self.assertEqual(
                measurement_step["inputs"]["prepared-context"]["artifact"]["id"],
                prepared["artifact"]["id"],
            )

            with self.assertRaisesRegex(ValueError, "outside the approved range"):
                workflow.submit_action(session_path, {
                    "id": "measure-context",
                    "parameters": {"target-context-tokens": 300000},
                    "reason": "attempt to escape the plan",
                })
            self.assertEqual(len(load_session(session_path)["action_results"]), 1)

            second = workflow.submit_action(session_path, {
                "id": "measure-context",
                "parameters": {"target-context-tokens": 200000},
                "reason": "probe a higher witnessed point",
            })
            self.assertEqual(second["state"], "awaiting-action")
            self.assertEqual(len(second["action_results"]), 2)
            self.assertEqual(len(load_session(session_path)["action_results"]), 2)

            ready = workflow.submit_action(session_path, {
                "id": "final-context-validation",
                "parameters": {"target-context-tokens": 200000},
                "reason": "validate the selected witnessed target",
            })
            self.assertEqual(ready["state"], "ready-to-finish")
            with self.assertRaisesRegex(ValueError, "not awaiting"):
                workflow.submit_action(session_path, {
                    "id": "measure-context",
                    "parameters": {"target-context-tokens": 131072},
                    "reason": "late mutation",
                })
            completed = workflow.finish(session_path, lambda: False)
            self.assertEqual(completed["state"], "complete")
            self.assertEqual(len(completed["action_results"]), 3)
            self.assertTrue(Path(completed["paths"]["report"]).is_file())
            report = Path(completed["paths"]["report"]).read_text()
            self.assertIn("## Question actions", report)
            self.assertIn('candidate `{"target-context-tokens":200000}`', report)
            self.assertIn("step `prepare-context` (preparation): complete", report)
            self.assertIn("artifact `prepared-context`", report)
            packet, _ = build_export(session_path)
            self.assertEqual(len(json.loads(packet)["action_evidence"]), 3)

    def test_action_specific_generation_requires_and_accepts_bound_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            entry = adaptive_question_entry()
            runner = FakeRunner()
            runner.dynamic_generation = True
            workflow = Workflow(
                entry, self.facts, self.facts_data, self._temper(workspace), runner
            )
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)
            awaiting = workflow.run(session_path, lambda: False)
            protocol = awaiting["action_results"][0]["answers"]
            self.assertEqual(protocol["workflow"]["state"], "observed")
            ready = workflow.submit_action(session_path, {
                "id": "final-context-validation",
                "parameters": {"target-context-tokens": 98304},
                "reason": "confirm the dynamically bound generation",
            })
            self.assertEqual(ready["state"], "ready-to-finish")
            completed = workflow.finish(session_path, lambda: False)
            packet, _ = build_export(session_path)
            self.assertEqual(completed["state"], "complete")
            self.assertEqual(len(json.loads(packet)["action_evidence"]), 2)

    def test_result_directed_controller_owns_the_next_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            entry = adaptive_question_entry(
                action_selection="result-directed",
                target_minimum=65536,
                target_maximum=122880,
                target_step=8192,
                target_default=98304,
            )
            runner = FakeRunner()
            runner.next_action_batches = [
                [{
                    "id": "measure-context",
                    "parameters": {"target-context-tokens": 106496},
                }],
                [{
                    "id": "final-context-validation",
                    "parameters": {"target-context-tokens": 106496},
                }],
                [],
            ]
            workflow = Workflow(
                entry,
                self.facts,
                self.facts_data,
                self._temper(workspace),
                runner,
            )
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)

            awaiting = workflow.run(session_path, lambda: False)
            self.assertEqual(awaiting["allowed_actions"], [{
                "id": "measure-context",
                "parameters": {"target-context-tokens": 106496},
            }])
            calls_before = len(runner.calls)
            with self.assertRaisesRegex(ValueError, "not issued by the bound package controller"):
                workflow.submit_action(session_path, {
                    "id": "measure-context",
                    "parameters": {"target-context-tokens": 114688},
                    "reason": "attempt a different in-range lattice point",
                })
            self.assertTrue(all("--dry-run" in call for call in runner.calls[calls_before:]))
            self.assertFalse(
                (Path(awaiting["paths"]["evidence"]) / "actions" / "attempt-0008").exists()
            )

            second = workflow.submit_action(session_path, {
                "id": "measure-context",
                "parameters": {"target-context-tokens": 106496},
                "reason": "run the controller-issued target",
            })
            self.assertEqual(second["state"], "awaiting-action")
            self.assertEqual(second["allowed_actions"], [{
                "id": "final-context-validation",
                "parameters": {"target-context-tokens": 106496},
            }])
            ready = workflow.submit_action(session_path, {
                "id": "final-context-validation",
                "parameters": {"target-context-tokens": 106496},
                "reason": "validate the controller-selected point",
            })
            self.assertEqual(ready["state"], "ready-to-finish")
            self.assertEqual(ready["allowed_actions"], [])

    def test_result_directed_controller_cannot_publish_an_off_lattice_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            entry = adaptive_question_entry(
                action_selection="result-directed",
                target_minimum=65536,
                target_maximum=122880,
                target_step=8192,
                target_default=98304,
            )
            runner = FakeRunner()
            runner.next_action_batches = [[{
                "id": "measure-context",
                "parameters": {"target-context-tokens": 100000},
            }]]
            workflow = Workflow(
                entry,
                self.facts,
                self.facts_data,
                self._temper(workspace),
                runner,
            )
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)

            with self.assertRaisesRegex(ValueError, "integer lattice"):
                workflow.run(session_path, lambda: False)

            failed = load_session(session_path)["attempts"][-1]
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["steps"][-1]["state"], "failed")

    def test_preparation_can_end_a_point_honestly_without_running_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            runner.preparation_terminal = True
            entry = adaptive_question_entry()
            workflow = Workflow(entry, self.facts, self.facts_data, self._temper(workspace), runner)
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)

            awaiting = workflow.run(session_path, lambda: False)

            action = next(
                attempt for attempt in awaiting["attempts"]
                if attempt["kind"] == "question-action"
            )
            self.assertEqual(action["state"], "complete")
            self.assertEqual(len(action["steps"]), 1)
            self.assertEqual(action["steps"][0]["kind"], "preparation")
            self.assertEqual(awaiting["answers"]["workflow"]["state"], "unknown")

    def test_prepared_artifact_byte_ceiling_stops_before_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            runner.artifact_padding = 1048577
            entry = adaptive_question_entry()
            workflow = Workflow(entry, self.facts, self.facts_data, self._temper(workspace), runner)
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)

            with self.assertRaisesRegex(ValueError, "artifact byte ceiling"):
                workflow.run(session_path, lambda: False)

            session = load_session(session_path)
            action = session["attempts"][-1]
            self.assertEqual(action["state"], "failed")
            self.assertEqual(len(action["steps"]), 1)

    def test_step_evidence_byte_ceiling_stops_before_another_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            runner.step_stdout_padding = 1048577
            workflow = Workflow(
                self.entry,
                self.facts,
                self.facts_data,
                self._temper(workspace),
                runner,
            )
            _, _, session_path = self._start(workflow, root, "keep")

            with self.assertRaisesRegex(ValueError, "step exceeded its evidence byte ceiling"):
                workflow.run(session_path, lambda: False)

            failed = load_session(session_path)["attempts"][-1]
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["failure"]["category"], "boundary")

    def test_resume_refuses_tampered_prepared_artifact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            entry = adaptive_question_entry()
            workflow = Workflow(entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            plan = workflow.plan(root, "keep")
            _, session_path = workflow.start(plan, plan.sha256)
            awaiting = workflow.run(session_path, lambda: False)
            action = next(
                attempt for attempt in awaiting["attempts"]
                if attempt["kind"] == "question-action"
            )
            manifest = Path(action["artifacts"]["prepared-context"]["root"]) / "manifest.json"
            manifest.write_bytes(manifest.read_bytes() + b"\n")

            with self.assertRaisesRegex(ValueError, "prepared artifact file differs"):
                workflow.resume(session_path)

    def test_resume_refuses_changed_exported_software_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            _, session, session_path = self._start(workflow, workspace / "run", "keep")
            Path(session["paths"]["software_lock"]).write_bytes(b"changed\n")
            with self.assertRaisesRegex(ValueError, "materialized execution input differs"):
                workflow.resume(session_path)

    def test_failed_stage_retains_output_without_advancing_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            runner = FakeRunner()
            runner.fail_contains = " fetch "
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), runner)
            _, _, session_path = self._start(workflow, root, "keep")
            with self.assertRaisesRegex(RuntimeError, "exited 7"):
                workflow.run(session_path, lambda: False)
            interrupted = load_session(session_path)
            self.assertEqual(interrupted["stages"][0]["state"], "complete")
            self.assertEqual(interrupted["stages"][1]["state"], "pending")
            self.assertEqual([attempt["state"] for attempt in interrupted["attempts"]], ["complete", "failed"])
            failed = Path(interrupted["paths"]["evidence"]) / "stages" / "02-fetch-model.stdout.failed"
            self.assertEqual(failed.read_bytes(), b"partial output\n")
            first_fetch = Path(interrupted["paths"]["evidence"]) / "attempts" / "attempt-0002.stdout"
            self.assertEqual(first_fetch.read_bytes(), b"partial output\n")
            runner.fail_contains = ""
            completed = workflow.run(session_path, lambda: False)
            self.assertEqual(completed["state"], "complete")
            fetch_attempts = [
                attempt for attempt in completed["attempts"]
                if attempt["stage"] == "02-fetch-model"
            ]
            self.assertEqual([attempt["state"] for attempt in fetch_attempts], ["failed", "complete"])
            self.assertEqual(fetch_attempts[1]["reason"], "resume-after-failure")

    def test_resume_refuses_a_changed_plan_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "run"
            workflow = Workflow(self.entry, self.facts, self.facts_data, self._temper(workspace), FakeRunner())
            _, _, session_path = self._start(workflow, root, "keep")
            session = load_session(session_path)
            plan_path = Path(session["paths"]["plan"])
            plan_path.write_bytes(plan_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "plan is not canonical"):
                workflow.resume(session_path)


if __name__ == "__main__":
    unittest.main()
