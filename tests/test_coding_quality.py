"""Coding repair feedback, bounded execution, and the one-shot measurement path."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fieldkit_runtime import check, workloads
from fieldkit_runtime.coding import evaluation, quality
from tests.test_check import RepositoryTest, FakeTemper, FACTS
from fieldkit_runtime.temper import Temper


def answer(tool="submit_patch", replacement="fixed\n"):
    arguments = {"explanation": "Repair the requested behavior.",
                 "edits": [{"path": "src/flask/helpers.py", "old_text": "original\n", "new_text": replacement}],
                 "new_files": [{"path": "tests/test_candidate_example.py", "content": "def test_change(): assert True\n"}]}
    return {"message": {"role": "assistant", "content": "", "reasoning": "inspect",
                        "tool_calls": [{"id": "call-1", "type": "function",
                                        "function": {"name": tool, "arguments": json.dumps(arguments)}}]},
            "stream_complete": True, "finish_reason": "tool_calls", "service_seconds": 1,
            "first_token_seconds": .2, "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


class QualityWorkflowTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "fixture"
        self.base = self.fixture / "base"
        (self.base / "src/flask").mkdir(parents=True)
        (self.base / "src/flask/helpers.py").write_text("original\n")
        self.stage = self.root / "case"
        self.stage.mkdir()
        self.case = {"id": "example", "request": {"messages": [
            {"role": "system", "content": "single response"}, {"role": "user", "content": "Fix it. Do not claim tests ran."}]},
            "edit_scope": {"existing_files": ["src/flask/helpers.py"], "new_file_prefixes": ["tests/test_candidate_"]},
            "original_tests": ["tests/test_original.py"], "oracle": "oracles/private.py",
            "candidate_test_prefix": "tests/test_candidate_"}
        self.row = {"failure": None}
        self.requests = []
        self.addCleanup(patch.stopall)
        patch.object(quality, "visible_control").start()

    def execute(self, responses, *, turns=8):
        responses = iter(responses)
        def request(folder, body, turn):
            self.requests.append(copy.deepcopy(body))
            response = next(responses)
            (folder / "response.json").write_text(json.dumps(response))
            return response
        return quality.run(self.case, self.stage, self.fixture, "/python", turns, time.time() + 30,
                           request, check.rates, self.row)

    def visible_tests(self, candidate, out, case, fixture, python, *, visible_only, deadline, observer=None):
        self.assertTrue(visible_only)
        good = (candidate / "src/flask/helpers.py").read_text() == "fixed\n"
        out.mkdir()
        log = out / "output.txt"
        log.write_text("passed" if good else "test_change: expected fixed, got broken")
        group = {"exit_code": 0 if good else 1, "summary": {"tests": 1, "failures": 0 if good else 1},
                 "output": str(log), "stopped": None}
        return {"all_pass": good, "groups": {"original": group, "candidate": group,
                                            "hidden": {"output": "DO NOT REVEAL"}}}

    def test_failed_proposal_gets_visible_feedback_and_can_be_repaired_before_submission(self):
        original = copy.deepcopy(self.case)
        with patch.object(evaluation, "evaluate", side_effect=self.visible_tests):
            final = self.execute([answer("test_patch", "broken\n"), answer("test_patch"), answer()])
        self.assertEqual(self.case, original)
        self.assertEqual((self.base / "src/flask/helpers.py").read_text(), "original\n")
        self.assertEqual(self.row["quality"]["status"], "submitted")
        self.assertEqual([t["status"] for t in self.row["quality"]["turns"]], ["tested", "tested", "submitted"])
        self.assertFalse(self.row["quality"]["turns"][0]["visible_tests_pass"])
        self.assertTrue(self.row["quality"]["turns"][1]["visible_tests_pass"])
        self.assertIn("expected fixed, got broken", self.requests[1]["messages"][-1]["content"])
        self.assertNotIn("DO NOT REVEAL", json.dumps(self.requests))
        self.assertEqual(final["message"]["tool_calls"][0]["function"]["name"], "submit_patch")
        self.assertTrue((self.stage / "turn-001/response.json").is_file())

    def test_turn_budget_keeps_first_failure_without_a_final_submission(self):
        with patch.object(evaluation, "evaluate", side_effect=self.visible_tests):
            self.execute([answer("test_patch", "broken\n")], turns=1)
        self.assertEqual(self.row["failure"]["kind"], "quality-budget-exhausted")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(json.loads((self.stage / "quality.json").read_text())["status"], "turn-budget-exhausted")

    def test_bad_patch_scope_gets_feedback_without_applying_it(self):
        bad = answer("test_patch")
        arguments = json.loads(bad["message"]["tool_calls"][0]["function"]["arguments"])
        arguments["edits"][0]["path"] = "../escape"
        bad["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps(arguments)
        with patch.object(evaluation, "evaluate", side_effect=AssertionError("invalid proposal must not run")):
            self.execute([bad, answer()])
        self.assertEqual(self.row["quality"]["turns"][0]["status"], "invalid-proposal")
        self.assertIn("error", self.requests[1]["messages"][-1]["content"])
        self.assertEqual(self.row["quality"]["status"], "submitted")

    def test_incomplete_stream_stops_instead_of_getting_a_free_retry(self):
        incomplete = answer("test_patch")
        incomplete.update(stream_complete=False, finish_reason=None)
        self.execute([incomplete])
        self.assertEqual(self.row["failure"]["kind"], "incomplete")
        self.assertEqual(len(self.requests), 1)

    def test_uncertain_evaluator_shutdown_retains_work_and_stops_the_loop(self):
        failure = evaluation.EvaluationFailure("cannot confirm shutdown", safe_to_cleanup=False)
        with patch.object(evaluation, "evaluate", side_effect=failure):
            with self.assertRaises(evaluation.EvaluationFailure):
                self.execute([answer("test_patch")])
        self.assertFalse(self.row["evaluator_safe_to_cleanup"])
        self.assertEqual(self.row["quality"]["status"], "invalid-evaluator")
        self.assertEqual(len(self.requests), 1)
        self.assertTrue((self.stage / "turn-001/candidate").exists())

    def test_exhausted_time_budget_makes_no_model_request(self):
        with patch.object(quality.time, "time", return_value=100):
            with self.assertRaises(TimeoutError):
                quality.run(self.case, self.stage, self.fixture, "/python", 8, 99,
                            Mock(side_effect=AssertionError("budget exhausted")), check.rates, self.row)
        self.assertEqual(self.row["quality"]["status"], "interrupted")

    def test_resource_failure_retains_the_attempt_without_leaving_quality_running(self):
        with patch.object(evaluation, "evaluate", side_effect=check.ProbeError("wired memory guard")):
            with self.assertRaisesRegex(check.ProbeError, "wired memory guard"):
                self.execute([answer("test_patch")])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(json.loads((self.stage / "quality.json").read_text())["status"], "runtime-failure")

    def test_resource_guard_stops_owned_grader_and_records_confirmed_shutdown(self):
        popen = subprocess.Popen
        children = []
        def spawn(argv, **kwargs):
            if argv[0] != "/usr/bin/sandbox-exec":
                return popen(argv, **kwargs)
            child = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            children.append(child)
            self.addCleanup(lambda: child.poll() is None and child.kill())
            return child
        observer = Mock(side_effect=[None, check.ProbeError("wired memory guard")])
        with patch.object(evaluation.subprocess, "Popen", side_effect=spawn):
            with self.assertRaisesRegex(check.ProbeError, "wired memory guard"):
                evaluation._run_group(self.base, self.stage, "guarded", [], sys.executable,
                                      self.base / "tests", self.base, observer=observer)
        self.assertIsNotNone(children[0].poll())
        result = json.loads((self.stage / "guarded/result.json").read_text())
        self.assertEqual(result["stopped"], "ProbeError")
        self.assertTrue(result["safe_to_cleanup"])
        self.assertEqual(result["remaining_processes"], [])

    def test_visible_evaluation_never_runs_or_exposes_independent_oracles(self):
        (self.base / "src/flask/__init__.py").write_text("")
        (self.base / "tests").mkdir()
        (self.base / "tests/test_candidate_example.py").write_text("def test_x(): pass\n")
        with patch.object(evaluation, "_run_group", return_value={
                "exit_code": 0, "summary": {"tests": 1, "skipped": 0}, "stopped": None}) as group:
            result = evaluation.evaluate(self.base, self.root / "visible", self.case, self.fixture, "/python",
                                         visible_only=True)
        self.assertNotIn("hidden", result["groups"])
        self.assertEqual([call.args[2] for call in group.call_args_list], ["original", "candidate"])
        for invocation in group.call_args_list:
            self.assertEqual(invocation.args[5], self.base / "tests")


class MeasurementModeTest(RepositoryTest):
    def setUp(self):
        super().setUp()
        self.binary = self.root / "temper"
        self.binary.write_text("fake")
        self.fake = FakeTemper()
        self.worker = check.BasicCheck(self.root, Temper(self.binary, runner=self.fake), workload="coding", case_id="async-stream")
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        evaluation.unpack(self.root / "tasks/flask-coding/flask.tar.gz", self.run_dir / "fixture")
        self.run = {"preset": {"id": "qwen", "family": "llama-server"}, "preparation": {"generation": "a" * 64}, "machine": FACTS}
        self.probe = Mock()
        self.probe.observe_engine.return_value = True
        self.probe.finish.return_value = {"safe_to_cleanup": True, "samples": 2, "max_gap_milliseconds": 1,
                                           "max_sleep_wake_milliseconds": 0, "issues": [], "stop_reasons": []}
        self.addCleanup(patch.stopall)
        patch.object(check, "ManagedProbe", return_value=self.probe).start()
        patch.object(check.watcher, "sample_macos", return_value={}).start()
        patch.object(check, "memory_record", return_value={}).start()
        self.response = answer()
        args = {"explanation": "test", "edits": [], "new_files": [
            {"path": "tests/test_candidate_async_stream_example.py", "content": "def test_example(): assert True\n"}]}
        self.response["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps(args)

    def test_performance_sends_one_streamed_request_and_never_runs_repair_tests(self):
        with patch.object(check, "monitored_call", side_effect=[{}, self.response]) as transport, \
                patch.object(quality, "run", side_effect=AssertionError("performance must not enter the quality loop")):
            row = self.worker.measure_case(self.run_dir, self.run, self.cases[0])
        self.assertEqual(row["status"], "patch-submitted")  # The coding oracle path is not a JSON-answer oracle.
        self.assertNotIn("quality", row)
        self.assertEqual(transport.call_count, 2)  # health + one completion
        body = transport.call_args.args[2]
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertTrue((self.run_dir / "cases/async-stream/patch.diff").exists())

    def test_uncertain_quality_grader_prevents_cleanup_even_when_engine_stopped(self):
        self.worker.workload.update(coding_mode="quality")
        self.worker.quality_evaluator = {"path": "/python"}
        def broken(*args, **kwargs):
            args[-1]["evaluator_safe_to_cleanup"] = False
            raise evaluation.EvaluationFailure("shutdown unknown", safe_to_cleanup=False)
        with patch.object(check, "monitored_call", return_value={}), patch.object(quality, "run", side_effect=broken):
            row = self.worker.measure_case(self.run_dir, self.run, self.cases[0])
        self.assertFalse(row["safe_to_cleanup"])
        self.assertEqual(row["status"], "runtime-failure")

    def test_quality_residency_preserves_turns_and_aggregates_work_without_fake_token_rates(self):
        self.worker.workload.update(coding_mode="quality")
        self.worker.quality_evaluator = {"path": "/python"}
        testing = copy.deepcopy(self.response)
        testing["message"]["tool_calls"][0]["function"]["name"] = "test_patch"
        result = {"all_pass": True, "groups": {"original": {}, "candidate": {}}}
        with patch.object(check, "monitored_call", side_effect=[{}, testing, self.response]), \
                patch.object(quality, "visible_control"), patch.object(evaluation, "evaluate", return_value=result):
            row = self.worker.measure_case(self.run_dir, self.run, self.cases[0])
        self.assertEqual(row["status"], "patch-submitted")
        self.assertEqual(row["quality"]["status"], "submitted")
        self.assertEqual(len(row["quality"]["turns"]), 2)
        self.assertEqual((row["input_tokens"], row["output_tokens"]), (200, 20))
        self.assertIsNone(row["generation_tokens_per_second"])
        self.assertEqual(self.probe.start.call_count, 1)
        self.assertEqual(self.probe.finish.call_count, 1)
        self.assertTrue((self.run_dir / "cases/async-stream/turn-001/request.json").exists())
        self.assertTrue((self.run_dir / "cases/async-stream/turn-002/response.json").exists())

    def test_smoke_requests_streaming_even_when_the_workload_does_not_specify_it(self):
        self.worker.workload.update(kind="smoke")
        case = workloads.cases(self.cases, "smoke")[0]
        response = copy.deepcopy(self.response)
        response["message"]["tool_calls"][0]["function"] = {"name": "record_answer", "arguments": '{"answer":"FIELD_KIT_OK"}'}
        with patch.object(check, "monitored_call", side_effect=[{}, response]) as transport:
            row = self.worker.measure_case(self.run_dir, self.run, case)
        self.assertEqual(row["status"], "passed")
        self.assertTrue(transport.call_args.args[2]["stream"])
