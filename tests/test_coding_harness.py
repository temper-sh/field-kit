"""The bundled coding harness through Field Kit's command driver, against a fake model server.

Real sandboxed tests grade real proposals; only the model is replaced. Failure
meanings: a known-good reference that does not pass means the grading broke; an
oracle or review file reaching the model means feedback leaks the answer.
"""
import contextlib
import copy
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

from fieldkit_runtime import grading
from fieldkit_runtime.adapters import models
from fieldkit_runtime.common import Refusal
from fieldkit_runtime.drivers import Harness
from fieldkit_runtime.process import CommandResult
from fieldkit_runtime.records import SessionFiles

REPOSITORY = Path(__file__).resolve().parents[1]
HARNESS = REPOSITORY / "harnesses/coding"
SANDBOX = Path("/usr/bin/sandbox-exec").is_file()
GRADING = grading.describe(REPOSITORY)["ready"]
sys.path.insert(0, str(HARNESS))
import harness  # noqa: E402
import quality  # noqa: E402

def reference(task):
    spec = harness.TASKS[task]
    return json.loads((HARNESS / "tasks" / spec["pack"] / "reference" / f"{spec['case']}.json").read_text())["proposal"]

class FakeModel:
    """Native template/tokenizer routes under a Temper-style prefix, and a streamed chat route."""
    def __init__(self, replies, prefix="/upstream/fake-model", native=True):
        self.replies, self.requests, self.native_paths = list(replies), [], []
        model = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if native and self.path == prefix + "/apply-template":
                    model.native_paths.append(self.path)
                    result = json.dumps({"prompt": json.dumps(body["messages"])}).encode()
                elif native and self.path == prefix + "/tokenize":
                    model.native_paths.append(self.path)
                    result = json.dumps({"tokens": list(range(len(body["content"]) // 4))}).encode()
                elif self.path == "/v1/chat/completions":
                    model.requests.append(body)
                    reply = model.replies.pop(0)
                    message = reply(body) if callable(reply) else copy.deepcopy(reply)
                    message, finish_reason = message if isinstance(message, tuple) else (message, "tool_calls")
                    for call in message["tool_calls"]:
                        call["index"] = 0
                    result = (b"data: " + json.dumps({"choices": [{"index": 0, "delta": message}]}).encode() + b"\n\n"
                              + b"data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}).encode()
                              + b"\n\ndata: [DONE]\n\n")
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.prefix = prefix

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server.server_port}"

def call(name, proposal, number):
    return {"role": "assistant", "content": "", "reasoning_content": f"retained reasoning {number}",
            "tool_calls": [{"id": f"call-{number}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(proposal)}}]}

@unittest.skipUnless(SANDBOX, "the coding harness grades inside the macOS sandbox")
class CodingHarnessTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def run_task(self, task, replies, *, adapter="openai/v1", window=65536, arguments=(), native=True):
        with FakeModel(replies, native=native) as fake:
            files = SessionFiles(self.root / task)
            files.stage.mkdir()
            engine = Mock(preset="fake-model")
            engine.probe.host, engine.probe.port = "127.0.0.1", fake.server.server_port
            engine.subject.native_prefix = fake.prefix
            engine.subject.context_window.return_value = window
            attempt = Mock()
            attempt.limit.return_value = 600
            attempt.engine.metrics = lambda response: {"input_tokens": (response.get("usage") or {}).get("prompt_tokens")}
            row = {}
            command = ["{python}", "-B", str(HARNESS / "harness.py"), "run", task, *arguments]
            Harness(command, str(HARNESS), models.resolve(adapter)).run(engine, files, row, attempt)
            result = json.loads((files.output / "result.json").read_text())
            return row, result, fake, files

    def test_pagination_repair_grades_the_reference_and_keeps_answers_from_the_model(self):
        proposal = reference("pagination-repair")
        wrong = copy.deepcopy(proposal)
        wrong["edits"][0]["old_text"] = "MISSING SOURCE"
        row, result, fake, files = self.run_task("pagination-repair", [
            call("test_patch", wrong, 0), call("test_patch", proposal, 1), call("submit_patch", proposal, 2)],
            adapter="flint/v1")
        self.assertEqual(row["command"]["returncode"], 0, files.stderr.read_text())
        self.assertEqual((result["state"], result["contract"], result["context_tokens"]), ("submitted", "original", 65536))
        graded = [item["evaluation"] for item in result["assessments"] if "evaluation" in item]
        self.assertTrue(graded and all(item["all_pass"] for item in graded), result["assessments"])
        self.assertIn("invalid_patch", result["assessments"][0])
        self.assertEqual(result["grading"]["environment"], "Field Kit Python, standard library only")
        self.assertEqual(set(fake.native_paths), {"/upstream/fake-model/apply-template", "/upstream/fake-model/tokenize"})
        history = [message for message in fake.requests[-1]["messages"] if message["role"] == "assistant"]
        self.assertEqual([message.get("reasoning_content") for message in history], ["retained reasoning 0", "retained reasoning 1"])
        self.assertNotIn("test_acceptance", json.dumps(fake.requests))
        self.assertEqual(fake.requests[0]["seed"], 421)
        self.assertNotIn("temperature", fake.requests[0])  # the preset's own sampling
        relayed = row["requests"]  # every harness request went through Field Kit's relay
        chats = [item for item in relayed["rows"] if item["path"] == "/v1/chat/completions"]
        self.assertEqual((len(chats), relayed["models"], relayed["other_models"]), (3, {"fake-model": 3}, []))
        self.assertTrue(all(item["stream_complete"] and item["finish_reason"] == "tool_calls" for item in chats))
        self.assertEqual(relayed["requests"], 3 + len(fake.native_paths))

    def test_a_prompt_treatment_changes_only_the_system_text(self):
        case = harness.load_case("pagination-repair")
        original, treated = quality.request_for(case, 6), quality.request_for(case, 6, "feedback-state")
        self.assertIn("Tool workspace contract", treated["messages"][0]["content"])
        treated["messages"][0] = original["messages"][0]
        self.assertEqual(treated, original)
        _, result, fake, _ = self.run_task("pagination-repair", [call("submit_patch", reference("pagination-repair"), 0)],
                                           arguments=("--contract", "feedback-state"))
        self.assertEqual(result["contract"], "feedback-state")
        self.assertIn("Tool workspace contract", fake.requests[0]["messages"][0]["content"])


    def test_an_engine_without_token_routes_needs_a_fixed_output_allowance(self):
        with self.assertRaises(FileNotFoundError):  # refused before the task starts or writes anything
            self.run_task("pagination-repair", [], native=False, window=None)
        self.assertIn("context window is unknown", (self.root / "pagination-repair/stderr.txt").read_text())
        shutil.rmtree(self.root / "pagination-repair")
        _, result, _, _ = self.run_task("pagination-repair", [], native=False)
        self.assertEqual(result["state"], "integration-failed")
        self.assertIn("--output-tokens", result["failure"]["message"])
        shutil.rmtree(self.root / "pagination-repair")
        _, result, fake, files = self.run_task("pagination-repair", [call("submit_patch", reference("pagination-repair"), 0)],
                                               native=False, window=None, arguments=("--output-tokens", "2048"))
        self.assertEqual(result["state"], "submitted")
        self.assertEqual(fake.requests[0]["max_tokens"], 2048)
        self.assertFalse(json.loads((files.output / "turn-001/budget.json").read_text())["token_preflight"])

    def test_submit_retained_proposal_grades_exact_files_without_retransmitting_them(self):
        proposal = reference("pagination-repair")
        def submit(body):
            receipt = json.loads(body["messages"][-1]["content"])
            return call("submit_tested_patch", {"proposal_id": receipt["proposal_id"], "explanation": "Ready."}, 1)
        row, result, fake, files = self.run_task("pagination-repair", [call("test_patch", proposal, 0), submit],
                                                 adapter="flint/v1")
        self.assertEqual(row["command"]["returncode"], 0, files.stderr.read_text())
        self.assertEqual(result["proposal_protocol"], "retained-v2")
        self.assertEqual(result["state"], "submitted")
        self.assertEqual(result["submission"]["source_turn"], "turn-001")
        first, submitted = result["assessments"]
        self.assertTrue(first["evaluation"]["all_pass"])
        self.assertEqual(submitted["proposal_id"], first["proposal_id"])
        self.assertTrue(submitted["same_patch_as_prior"])
        self.assertEqual(submitted["source_turn"], "turn-001")
        args = json.loads(json.loads((files.output / "turn-002/response.json").read_text())[
            "message"]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(set(args), {"proposal_id", "explanation"})
        self.assertEqual((files.output / "turn-001/candidate/pagination.py").read_bytes(),
                         (files.output / "assessment-turn-001/candidate/pagination.py").read_bytes())
        self.assertNotIn("test_acceptance", json.dumps(fake.requests))

    def test_unknown_reference_is_rejected_and_a_known_earlier_proposal_can_be_selected(self):
        good = reference("pagination-repair")
        bad = copy.deepcopy(good)
        bad["new_files"][0]["content"] = "import unittest\nclass Broken(unittest.TestCase):\n def test_wrong(self): self.fail('wrong expectation')\n"
        selected = {}
        def test_later(body):
            selected["proposal_id"] = json.loads(body["messages"][-1]["content"])["proposal_id"]
            return call("test_patch", bad, 2)
        def submit_earlier(body):
            return call("submit_tested_patch", {**selected, "explanation": "Select the first tested proposal."}, 3)
        _, result, _, files = self.run_task("pagination-repair", [
            call("submit_tested_patch", {"proposal_id": "unknown", "explanation": "Ready."}, 0),
            call("test_patch", good, 1), test_later, submit_earlier])
        self.assertEqual(result["state"], "submitted", files.stderr.read_text())
        self.assertEqual(result["quality"]["turns"][0]["status"], "invalid-proposal")
        self.assertIn("invalid_patch", result["assessments"][0])
        self.assertEqual(result["submission"]["source_turn"], "turn-002")
        self.assertTrue(result["assessments"][1]["evaluation"]["all_pass"])
        self.assertFalse(result["assessments"][2]["evaluation"]["all_pass"])
        self.assertEqual(result["assessments"][-1]["proposal_id"], result["assessments"][1]["proposal_id"])

    def test_truncated_final_response_keeps_the_correct_tested_artifact_without_claiming_submission(self):
        def truncated(body):
            return call("submit_patch", {"partial": "unfinished"}, 1), "length"
        _, result, _, files = self.run_task("pagination-repair", [
            call("test_patch", reference("pagination-repair"), 0), truncated])
        self.assertEqual(result["state"], "incomplete", files.stderr.read_text())
        self.assertNotIn("submission", result)
        self.assertTrue(result["assessments"][0]["evaluation"]["all_pass"])
        self.assertIn("proposal_id", result["assessments"][0])
        self.assertTrue((files.output / "assessment-turn-001/proposal.patch").is_file())

    def test_submitting_a_failed_tested_proposal_does_not_turn_its_grading_into_a_pass(self):
        proposal = reference("pagination-repair")
        proposal["new_files"][0]["content"] = "import unittest\nclass Broken(unittest.TestCase):\n def test_wrong(self): self.fail('wrong expectation')\n"
        def submit(body):
            receipt = json.loads(body["messages"][-1]["content"])
            self.assertFalse(receipt["visible_tests_pass"])
            return call("submit_tested_patch", {"proposal_id": receipt["proposal_id"],
                                               "explanation": "Submit for independent review."}, 1)
        _, result, _, files = self.run_task("pagination-repair", [call("test_patch", proposal, 0), submit])
        self.assertEqual(result["state"], "submitted", files.stderr.read_text())
        self.assertFalse(result["assessments"][0]["evaluation"]["all_pass"])
        self.assertEqual(result["source_review"], "pending")

    def test_legacy_full_patch_protocol_is_explicitly_replayable(self):
        proposal = reference("pagination-repair")
        _, result, fake, files = self.run_task("pagination-repair", [
            call("test_patch", proposal, 0), call("submit_patch", proposal, 1)],
            arguments=("--proposal-protocol", "complete-v1"))
        self.assertEqual(result["state"], "submitted", files.stderr.read_text())
        self.assertEqual(result["proposal_protocol"], "complete-v1")
        self.assertNotIn("submit_tested_patch", json.dumps(fake.requests[0]))
        self.assertNotIn("proposal_id", result["assessments"][0])

    def test_zero_limits_allow_a_seventh_response_without_a_client_deadline(self):
        replies = [call("submit_tested_patch", {"proposal_id": "unknown", "explanation": "Ready."}, n)
                   for n in range(6)]
        replies.append(call("submit_patch", reference("pagination-repair"), 6))
        _, result, fake, files = self.run_task("pagination-repair", replies,
                                               arguments=("--seconds", "0", "--turns", "0"))
        self.assertEqual(result["state"], "submitted", files.stderr.read_text())
        self.assertEqual((result["budget_seconds"], result["max_turns"]), (0, 0))
        self.assertIsNone(result["quality"]["deadline"])
        self.assertEqual(len(fake.requests), 7)
        self.assertNotIn("at most", fake.requests[0]["messages"][0]["content"])

    @unittest.skipUnless(GRADING, "run ./field-kit grading to install the Flask test packages")
    def test_flask_references_pass_independent_tests_and_review_checks(self):
        for task in ("flask-template-decorators", "flask-async-stream"):
            with self.subTest(task=task):
                _, result, fake, files = self.run_task(task, [call("submit_patch", reference(task), 0)])
                self.assertEqual(result["state"], "submitted", files.stderr.read_text())
                assessment = result["assessments"][0]
                self.assertTrue(assessment["evaluation"]["behavior_pass"], assessment)
                self.assertEqual(assessment["review"]["exit_code"], 0, assessment["review"])
                self.assertEqual(result["grading"]["environment"]["packages"]["pytest"], "8.4.1")
                case = harness.load_case(task)
                text = json.dumps(fake.requests)
                self.assertNotIn(Path(case["oracle"]).name, text)
                self.assertTrue(all(path.name not in text for path in harness.review_checks(task, case)[1]))

    @unittest.skipUnless(GRADING, "run ./field-kit grading to install the Flask test packages")
    def test_the_unchanged_flask_source_fails_the_independent_tests(self):
        for task in ("flask-template-decorators", "flask-async-stream"):
            with self.subTest(task=task):
                spec, case = harness.TASKS[task], harness.load_case(task)
                fixture = self.root / ("fixture-" + task)
                base = harness.evaluate_flask.unpack(HARNESS / "tasks/flask" / spec["archive"], fixture)
                result = harness.evaluate_flask.evaluate(base, self.root / ("base-" + task), case, fixture,
                                                         str(harness.GRADING), require_candidate_tests=False)
                self.assertTrue(harness.evaluate_flask.group_passes(result["groups"]["original"]))
                self.assertFalse(result["behavior_pass"])

@unittest.skipUnless(SANDBOX, "the coding harness grades inside the macOS sandbox")
class UnittestEntryModuleTest(unittest.TestCase):
    def test_an_external_entry_module_is_used_by_each_test_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, candidate = root / "fixture", root / "candidate"
            baseline = fixture / "base"
            (baseline / "tests").mkdir(parents=True)
            (fixture / "oracle").mkdir()
            (baseline / "ledger.py").write_text("def total(values): return sum(values)\n")
            original = "import unittest\nfrom ledger import total\nclass Check(unittest.TestCase):\n def test_total(self): self.assertEqual(total([7]), 7)\n"
            independent = original.replace("[7]), 7", "[2, 3]), 5")
            (baseline / "tests/test_original.py").write_text(original)
            (fixture / "oracle/test_acceptance.py").write_text(independent)
            shutil.copytree(baseline, candidate)
            (candidate / "tests/test_candidate_empty.py").write_text(original.replace("[7]), 7", "[]), 0"))
            case = {"id": "external-ledger", "original_tests": ["tests/test_original.py"],
                    "oracle": "oracle/test_acceptance.py"}
            evaluator = harness.evaluate_unittest.UnittestEvaluation("ledger")
            correct = evaluator.evaluate(candidate, root / "correct", case, fixture, sys.executable)
            self.assertTrue(correct["all_pass"], correct)
            self.assertTrue(all(group["summary"]["tests"] == 1 for group in correct["groups"].values()))
            (candidate / "ledger.py").write_text("def total(values): return max(values, default=0)\n")
            wrong = evaluator.evaluate(candidate, root / "wrong", case, fixture, sys.executable)
            self.assertTrue(evaluator.group_passes(wrong["groups"]["original"]))
            self.assertTrue(evaluator.group_passes(wrong["groups"]["candidate"]))
            self.assertFalse(wrong["behavior_pass"])
            self.assertEqual(wrong["groups"]["hidden"]["summary"]["failures"], 1)
            self.assertEqual(wrong["groups"]["hidden"]["summary"]["errors"], 0)


class FrozenInputTest(unittest.TestCase):

    def test_a_changed_task_input_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            copy_root = Path(temp) / "coding"
            shutil.copytree(HARNESS, copy_root, ignore=shutil.ignore_patterns("__pycache__"))
            oracle = copy_root / "tasks/pagination/reference/pagination-repair.json"
            oracle.write_text(oracle.read_text().replace("pagination", "pagination ", 1))
            original = harness.ROOT
            harness.ROOT = copy_root
            try:
                with self.assertRaisesRegex(RuntimeError, "differ from manifest"):
                    harness.verify()
            finally:
                harness.ROOT = original
        self.assertTrue(harness.verify()["verified"])

class GradingEnvironmentTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repository = Path(temp.name)
        requirements = self.repository / grading.REQUIREMENTS
        requirements.parent.mkdir(parents=True)
        requirements.write_text("pytest==8.4.1 --hash=sha256:" + "a" * 64 + "\n")
        self.calls = []
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def fake_run(self, argv, timeout):
        self.calls.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[3]) / "bin").mkdir(parents=True)
            (Path(argv[3]) / "bin/python").write_text("")
        return CommandResult(b"", b"", 0)

    def capture(self, argv, timeout):
        return CommandResult(json.dumps([{"name": "pytest", "version": "8.4.1"}, {"name": "pip", "version": "26"}]).encode(), b"", 0)

    def test_prepared_once_then_ready_and_rebuilt_when_requirements_change(self):
        state = grading.prepare(self.repository, run=self.fake_run, capture=self.capture)
        self.assertEqual((state["ready"], state["packages"]), (True, {"pytest": "8.4.1"}))
        install = self.calls[1]
        self.assertIn("--require-hashes", install)
        self.assertIn("--only-binary=:all:", install)
        self.assertFalse((self.repository / ".local/grading.partial").exists())
        self.calls.clear()
        grading.prepare(self.repository, run=self.fake_run, capture=self.capture)
        self.assertEqual(self.calls, [])
        (self.repository / grading.REQUIREMENTS).write_text("pytest==8.4.2 --hash=sha256:" + "b" * 64 + "\n")
        self.assertFalse(grading.describe(self.repository)["ready"])
        grading.prepare(self.repository, run=self.fake_run, capture=self.capture)
        self.assertTrue(grading.describe(self.repository)["ready"])

    def test_a_folder_not_made_by_field_kit_is_never_replaced(self):
        (self.repository / ".local/grading").mkdir(parents=True)
        with self.assertRaisesRegex(Refusal, "not made by Field Kit"):
            grading.prepare(self.repository, run=self.fake_run, capture=self.capture)

if __name__ == "__main__":
    unittest.main()
