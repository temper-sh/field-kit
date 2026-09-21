"""Consent, bounded attempts, attributable results and safe recovery."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fieldkit_runtime.catalog import Refusal, canonical_json, digest, parse_machine_facts
from fieldkit_runtime.workflow import (CommandResult, CommandFailure, Workflow, _exclusive_session_lock,
                                      _runtime_digest, build_export, load_session)
from tests.fixtures import question_entry, adaptive_question_entry
from tests.test_catalog import facts_bytes


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.fail_contains = ""
        self.next_actions = None
        self.next_action_batches = []
        self.stdout_padding = 0
        self.protocol_answer = {"state": "observed", "value": {"completed": True}}
        self.safe = True

    def execution(self, lock):
        raw = lock.read_bytes()
        try:
            value = json.loads(raw)
            profile = value["selection"]["profile"]
            defaults = {key: row["request_defaults"] for key, row in value["records"]["layouts"].items()}
        except ValueError:
            profile, defaults = "fixture", {"fixture-layout": {}}
        return {"schema": "temper-execution/v1", "profile": profile, "layouts": sorted(defaults),
                "lock_sha256": digest(raw), "execution_digest": "a" * 64, "request_defaults": defaults}

    def __call__(self, arguments, timeout):
        argv = list(arguments)
        self.calls.append(argv)
        if self.fail_contains and self.fail_contains in " ".join(argv):
            return CommandResult(b"partial output\n", b"injected failure\n", 7)
        if argv[-1:] == ["version"]:
            return CommandResult(b"temper 0.1.0-alpha.7\n", b"", 0)
        if argv[1:3] == ["execution", "inspect"]:
            return CommandResult(canonical_json(self.execution(Path(argv[argv.index("--lock") + 1]))), b"", 0)
        if argv[1:3] == ["execution", "prepare"]:
            return CommandResult(canonical_json({"schema": "temper-execution-material/v1",
                "execution": self.execution(Path(argv[argv.index("--lock") + 1])), "generation": "b" * 64,
                "binding": "schema: temper-field-kit-binding/v1\nfixture: true\n"}), b"", 0)
        if argv[1:3] == ["execution", "remove"]:
            return CommandResult(b"RESULT software-remove changed\n", b"", 0)
        if "--action" in argv:
            action = Path(argv[argv.index("--action") + 1])
            session = json.loads(Path(argv[argv.index("--session") + 1]).read_bytes())
            report = {"schema": "field-kit-action-result/v2", "status": "complete",
                      "action_sha256": digest(action.read_bytes()), "plan_sha256": session["plan"]["sha256"],
                      "answers": {"workflow": self.protocol_answer},
                      "next_actions": self.next_action_batches.pop(0) if self.next_action_batches else self.next_actions,
                      "protocol": {"schema": "field-kit-fixture-protocol/v1", "status": "complete",
                                   "model": "fixture-layout", "generation": session["generation"], "safe_to_cleanup": self.safe}}
            Path(argv[argv.index("--report") + 1]).write_bytes(canonical_json(report))
            return CommandResult(b"x" * self.stdout_padding, b"", 0)
        raise AssertionError(argv)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="field kit workflow ")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.temper = self.parent / "temper"
        self.temper.write_bytes(b"fixture temper")
        self.raw = facts_bytes()
        self.runner = FakeRunner()

    def workflow(self, entry=None):
        entry = entry or question_entry()
        package_root = self.parent / "package"
        package_root.mkdir(exist_ok=True)
        for name, raw in {"package.json": entry.package_data, **entry.files}.items():
            (package_root / name).write_bytes(raw)
        entry = replace(entry, package_root=package_root)
        return Workflow(entry, parse_machine_facts(self.raw), self.raw, self.temper, self.runner, lambda _: None)

    def start(self, workflow, outcome="keep"):
        plan = workflow.plan(self.parent / "installation", outcome)
        return workflow.start(plan, plan.sha256)[1]

    def test_planning_is_read_only_and_approval_binds_exact_inputs(self):
        workflow = self.workflow()
        plan = workflow.plan(self.parent / "installation", "keep")
        self.assertFalse((self.parent / "installation").exists())
        with self.assertRaisesRegex(Refusal, "approval"):
            workflow.start(plan, "wrong")
        self.assertTrue(all(c[-1] == "version" or c[1:3] == ["execution", "inspect"] for c in self.runner.calls))

    def test_fixed_question_completes_exports_and_second_run_does_not_repeat_work(self):
        workflow = self.workflow()
        path = self.start(workflow, "restore")
        result = workflow.run(path, lambda: True)
        self.assertEqual(result["cleanup"], "complete")
        self.assertFalse(Path(result["paths"]["root"]).exists())
        before = len(self.runner.calls)
        self.assertEqual(workflow.run(path, lambda: self.fail("asked again")), result)
        self.assertEqual(len(self.runner.calls), before)
        packet, _ = build_export(path)
        evidence = json.loads(packet)["action_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["report"]["answers"], result["answers"])
        self.assertNotIn("steps", evidence[0])

    def test_failed_setup_keeps_attempt_output_and_retries_only_setup(self):
        workflow = self.workflow()
        path = self.start(workflow)
        self.runner.fail_contains = "execution prepare"
        with self.assertRaises(CommandFailure): workflow.run(path, lambda: True)
        failed = load_session(path)["attempts"][0]
        self.assertEqual(Path(failed["evidence"]["stdout"]).read_bytes(), b"partial output\n")
        self.runner.fail_contains = ""
        result = workflow.run(path, lambda: True)
        self.assertEqual([a["state"] for a in result["attempts"][:2]], ["failed", "complete"])
        self.assertEqual(sum("--action" in call for call in self.runner.calls), 1)

    def test_negative_answer_is_valid_evidence(self):
        workflow = self.workflow()
        self.runner.protocol_answer = {"state": "failed", "reason": "wrong answer"}
        result = workflow.run(self.start(workflow), lambda: True)
        self.assertEqual(result["answers"]["workflow"]["state"], "failed")
        self.assertEqual(result["state"], "complete")

    def test_resume_refuses_changed_lock_plan_binary_or_report(self):
        for target in ("lock", "plan", "binary", "report"):
            with self.subTest(target=target):
                # Each case owns separate paths and runtime inputs.
                base = self.parent / target; base.mkdir()
                prior = self.parent; self.parent = base
                workflow = self.workflow(adaptive_question_entry())
                path = self.start(workflow)
                session = workflow.run(path, lambda: True)
                file = {"lock": Path(session["paths"]["execution_lock"]), "plan": Path(session["plan"]["path"]),
                        "binary": self.temper, "report": Path(session["attempts"][-1]["protocol_report"]["path"])}[target]
                original = file.read_bytes(); file.write_bytes(original + b"changed")
                try:
                    with self.assertRaises((Refusal, ValueError)): workflow.resume(path)
                finally: file.write_bytes(original); self.parent = prior

    def test_adaptation_requires_final_validation_and_retains_each_target(self):
        workflow = self.workflow(adaptive_question_entry())
        path = self.start(workflow)
        self.assertEqual(workflow.run(path, lambda: True)["state"], "awaiting-action")
        with self.assertRaises(Refusal): workflow.finish(path, lambda: True)
        workflow.submit_action(path, {"id": "measure-context", "parameters": {"target-context-tokens": 65536}, "reason": "narrow the target"})
        workflow.submit_action(path, {"id": "final-context-validation", "parameters": {"target-context-tokens": 65536}, "reason": "validate selected target"})
        result = workflow.finish(path, lambda: True)
        attempts = [a for a in result["attempts"] if a["kind"] == "question-action"]
        self.assertEqual([a["candidate"]["target-context-tokens"] for a in attempts], [98304, 65536, 65536])

    def test_controller_frontier_and_integer_bounds_remain_enforced(self):
        workflow = self.workflow(adaptive_question_entry(action_selection="result-directed"))
        path = self.start(workflow)
        self.runner.next_actions = [{"id": "final-context-validation", "parameters": {"target-context-tokens": 98304}}]
        workflow.run(path, lambda: True)
        with self.assertRaisesRegex(Refusal, "not issued"):
            workflow.submit_action(path, {"id": "measure-context", "parameters": {"target-context-tokens": 65536}, "reason": "unissued"})
        self.runner.next_actions = []
        workflow.submit_action(path, {"id": "final-context-validation", "parameters": {"target-context-tokens": 98304}, "reason": "issued"})
        self.assertEqual(workflow.finish(path, lambda: True)["state"], "complete")

    def test_unknown_shutdown_prevents_cleanup(self):
        workflow = self.workflow()
        path = self.start(workflow, "restore")
        self.runner.safe = False
        with self.assertRaisesRegex(Refusal, "shutdown"): workflow.run(path, lambda: True)
        self.assertTrue(Path(load_session(path)["paths"]["root"]).exists())
        self.assertFalse(any(c[1:3] == ["execution", "remove"] for c in self.runner.calls))

    def test_later_uncommitted_action_cannot_reuse_earlier_shutdown_evidence(self):
        workflow = self.workflow(adaptive_question_entry())
        path = self.start(workflow)
        workflow.run(path, lambda: True)
        self.runner.fail_contains = "--action"
        with self.assertRaises(CommandFailure):
            workflow.submit_action(path, {"id": "measure-context", "parameters": {"target-context-tokens": 65536}, "reason": "next"})
        self.runner.fail_contains = ""
        with self.assertRaisesRegex(Refusal, "shutdown"):
            workflow.submit_action(path, {"id": "final-context-validation", "parameters": {"target-context-tokens": 98304}, "reason": "finish"})
        session = load_session(path)
        attempt = session["attempts"][-1]
        attempt["state"] = "running"
        used = session["action_elapsed_seconds"]
        path.write_bytes(canonical_json(session))
        resumed = workflow.resume(path)
        self.assertEqual(resumed["attempts"][-1]["state"], "interrupted")
        self.assertEqual(resumed["action_elapsed_seconds"], round(used + attempt["timeout_seconds"], 6))

    def test_action_evidence_ceiling_prevents_another_effect(self):
        workflow = self.workflow()
        path = self.start(workflow)
        self.runner.stdout_padding = 1048577
        with self.assertRaisesRegex(Refusal, "ceiling"): workflow.run(path, lambda: True)
        self.assertFalse(any(c[1:3] == ["execution", "remove"] for c in self.runner.calls))

    def test_session_lock_excludes_a_second_invocation(self):
        lock = self.parent / "lock"; lock.touch()
        with _exclusive_session_lock(lock):
            with self.assertRaises(Refusal):
                with _exclusive_session_lock(lock): pass

    def test_runtime_identity_includes_experiment_code(self):
        runtime = self.parent / "runtime"; (runtime / "experiments").mkdir(parents=True)
        entry = runtime / "workflow.py"; entry.write_text("workflow")
        method = runtime / "experiments/method.py"; method.write_text("before")
        with patch("fieldkit_runtime.workflow.__file__", str(entry)):
            before = _runtime_digest(); method.write_text("after")
            self.assertNotEqual(_runtime_digest(), before)
