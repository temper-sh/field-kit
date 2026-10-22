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
                    message = model.replies.pop(0)
                    for call in message["tool_calls"]:
                        call["index"] = 0
                    result = (b"data: " + json.dumps({"choices": [{"index": 0, "delta": message}]}).encode() + b"\n\n"
                              + b"data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}).encode()
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
