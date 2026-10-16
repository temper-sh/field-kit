"""The basic check's decisions and run states, with a fake Temper and no model."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from fieldkit_runtime import check, watcher
from fieldkit_runtime.check import BasicCheck
from fieldkit_runtime.coding.patches import InvalidPatch
from fieldkit_runtime.common import Refusal
from fieldkit_runtime.process import CommandResult
from fieldkit_runtime.serving import HTTPResponseError
from fieldkit_runtime.temper import Temper, version_at_least

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
FACTS = {"schema": "temper-machine-facts/v1", "chip": "Apple M5", "physical_memory_bytes": 32 * GIB,
         "wired_limit_mib": 24576, "target": {"distribution_version": "26.6.1"}}
ACTIVE = {"sha256": "c" * 64, "sequence": 2}


def catalog():
    def preset(engine, group, interface="chat-completions"):
        return {"engine": engine, "interface": interface, "presentation": {"group": group, "display_name": engine + " " + group}}
    return {"schema": "temper-catalog/v1", "engines": {"llama-cpp": {"adapter": "llama-server/v1"}, "splash": {"adapter": "splash/v1"}},
            "presets": {"zeta-new": preset("llama-cpp", "general"), "qwen3.8-27b-q4xl-mtp": preset("llama-cpp", "general"),
                        "qwen3.8-27b-q4xl-splash": preset("splash", "general"), "translator": preset("llama-cpp", "specialist"),
                        "reranker": preset("llama-cpp", "general", "reranking")}}


class FakeTemper:
    """Answers each Temper command with the document 0.2.0 prints for it."""
    def __init__(self, model_bytes=None, can_prepare=None, cached=True):
        self.model_bytes = {"qwen3.8-27b-q4xl-mtp": 17 * GIB, "qwen3.8-27b-q4xl-splash": 20 * GIB, "zeta-new": 30 * GIB, **(model_bytes or {})}
        self.can_prepare = can_prepare or {}
        self.cached = cached
        self.calls = []

    def __call__(self, argv, timeout, environment=None):
        arguments = argv[1:]
        self.calls.append((arguments, environment))
        def value(flag):
            return arguments[arguments.index(flag) + 1]
        if arguments == ["version"]:
            return CommandResult(b"temper 0.2.0\n", b"", 0)
        command = tuple(arguments[:2])
        if command == ("machine", "facts"):
            document = FACTS
        elif command == ("catalog", "update"):
            Path(value("--root")).mkdir(parents=True, exist_ok=True)
            document = {"active": ACTIVE, "latest": ACTIVE}
        elif command == ("catalog", "inspect"):
            document = {"active": ACTIVE, "catalog": catalog()}
        elif command == ("init", "--dry-run"):
            preset = value("--preset")
            private = environment is not None and "hf-cache" in environment.get("HF_HUB_CACHE", "")
            model = {"name": "model.gguf", "bytes": self.model_bytes[preset], "kind": "model"}
            remaining = [] if self.cached and not private else [model]
            remaining.append({"name": "template.jinja", "bytes": 28000, "kind": "template"})
            document = {"schema": "temper-configuration-result/v1", "plan": {
                "can_prepare": self.can_prepare.get(preset, True), "refusals": [] if self.can_prepare.get(preset, True) else ["needs Apple M3"],
                "presets": {preset: {"model_bytes": self.model_bytes[preset]}},
                "download_bytes": self.model_bytes[preset] + 28000, "remaining_downloads": remaining,
                "remaining_download_bytes": sum(item["bytes"] for item in remaining),
                "fresh_disk_bytes": self.model_bytes[preset], "remaining_disk_bytes": sum(item["bytes"] for item in remaining),
                "free_disk_bytes": 90 * GIB}}
        elif command == ("catalog", "compile"):
            Path(value("--out")).write_text('{"schema": "temper-execution-lock/v1"}\n')
            document = {"schema": "temper-catalog-compilation/v1", "preset": value("--preset"), "profile_min_memory_gib": 32}
        elif command == ("execution", "inspect"):
            document = self.execution()
        elif command == ("execution", "prepare"):
            document = {"schema": "temper-execution-material/v1", "execution": self.execution(), "generation": "a" * 64,
                        "binding": "schema: temper-field-kit-binding/v1\n"}
        elif command == ("execution", "remove"):
            document = {"removed": True}
        else:
            return CommandResult(b"", b"unknown command " + " ".join(arguments).encode(), 2)
        return CommandResult(json.dumps(document).encode(), b"", 0)

    def execution(self):
        return {"schema": "temper-execution/v1", "preset": "qwen3.8-27b-q4xl-mtp", "presets": ["qwen3.8-27b-q4xl-mtp"]}


class Harness(BasicCheck):
    """Replaces only the model measurement; every other step runs."""
    def __init__(self, repository, fake, rows, answer="yes"):
        binary = repository / "temper"
        binary.write_bytes(b"fake temper")
        super().__init__(repository, Temper(binary, runner=fake, progress_runner=fake),
                         ask=lambda _: answer, say=self.lines.append if hasattr(self, "lines") else (lambda _: None))
        self.rows = list(rows)
        self.measured = []

    def measure_case(self, directory, run, case):
        self.measured.append(case["id"])
        row = {"id": case["id"], "status": "patch-submitted", "failure": None, "safe_to_cleanup": True}
        row.update(self.rows.pop(0) if self.rows else {})
        return row


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / "protocols", self.root / "protocols")
        shutil.copytree(ROOT / "tasks", self.root / "tasks")
        self.protocol, self.cases, _ = check.load_protocol(self.root)


class DecisionTest(RepositoryTest):
    def test_versions_below_0_2_0_and_its_prereleases_are_refused(self):
        for text, accepted in (("temper 0.2.0", True), ("temper 0.2.1", True), ("temper 1.0.0", True),
                               ("temper 0.0.0-dev", True), ("temper 0.1.0", False), ("temper 0.2.0-alpha.1", False)):
            with self.subTest(text=text):
                self.assertEqual(version_at_least(text), accepted)
        with self.assertRaises(Refusal):
            version_at_least("Temper version unknown")

    def test_general_chat_presets_follow_the_protocol_order_then_new_ones(self):
        self.assertEqual(check.eligible_presets(catalog(), self.protocol),
                         ["qwen3.8-27b-q4xl-mtp", "qwen3.8-27b-q4xl-splash", "zeta-new"])

    def test_memory_floor_uses_three_quarters_of_memory_and_the_wired_limit(self):
        plan = {"can_prepare": True, "presets": {"p": {"model_bytes": 20 * GIB}}}
        self.assertIsNone(check.admission(plan, "p", FACTS, self.protocol))  # 20 + 4 <= 24
        plan["presets"]["p"]["model_bytes"] = 20 * GIB + 1
        self.assertIn("this Mac allows 24.0 GiB", check.admission(plan, "p", FACTS, self.protocol))
        small_wired = {**FACTS, "wired_limit_mib": 16384}
        plan["presets"]["p"]["model_bytes"] = 13 * GIB
        self.assertIn("allows 16.0 GiB", check.admission(plan, "p", small_wired, self.protocol))
        refused = {"can_prepare": False, "refusals": ["needs Apple M3"], "presets": {"p": {"model_bytes": GIB}}}
        self.assertEqual(check.admission(refused, "p", FACTS, self.protocol), "needs Apple M3")

    def test_choice_skips_blocked_presets_and_refuses_an_explicit_blocked_one(self):
        rows = [{"preset": "a", "blocked": "too large"}, {"preset": "b", "blocked": None}]
        self.assertEqual(check.choose(rows, None)["preset"], "b")
        with self.assertRaisesRegex(Refusal, "too large"):
            check.choose(rows, "a")
        with self.assertRaisesRegex(Refusal, "covers these presets: a, b"):
            check.choose(rows, "translator")
        with self.assertRaisesRegex(Refusal, "no preset"):
            check.choose([{"preset": "a", "blocked": "too large"}], None)

    def test_shared_cache_only_when_every_model_file_is_present(self):
        template = {"kind": "template", "bytes": 28000}
        self.assertEqual(check.cache_mode({"remaining_downloads": [template]}), "shared")
        self.assertEqual(check.cache_mode({"remaining_downloads": [template, {"kind": "model", "bytes": 1}]}), "private")
        self.assertEqual(check.cache_mode({"remaining_downloads": [{"kind": "template", "bytes": 2 * 1024**2}]}), "private")
        self.assertEqual(check.cache_mode({"remaining_downloads": [{"kind": "software", "bytes": GIB}]}), "shared")

    def test_watch_limits_are_valid_and_splash_adds_its_frontend(self):
        llama = check.watch_spec(self.protocol, FACTS, "llama-server")
        splash = check.watch_spec(self.protocol, FACTS, "splash")
        for spec in (llama, splash):
            watcher.validate_watch_spec(spec)
        self.assertEqual([role["id"] for role in llama["roles"]], ["engine", "router"])
        self.assertEqual([role["id"] for role in splash["roles"]], ["engine", "frontend", "router"])
        self.assertEqual(llama["roles"][0]["rss_bytes_max"], 28 * GIB)

    def test_failures_are_classified_without_guessing(self):
        self.assertEqual(check.classify_failure(KeyboardInterrupt()), "interrupted")
        self.assertEqual(check.classify_failure(TimeoutError("request reached the approved time limit")), "timeout")
        self.assertEqual(check.classify_failure(HTTPResponseError("model HTTP status 400: b'request exceeds the available context size'")), "exceeds-context")
        self.assertEqual(check.classify_failure(RuntimeError("ggml: out of memory")), "memory-failure")
        self.assertEqual(check.classify_failure(RuntimeError("process watcher fired a safety stop")), "stopped-by-watch")
        self.assertEqual(check.classify_failure(RuntimeError("connection reset")), "runtime-failure")

    def test_rates_come_only_from_engine_counters(self):
        response = {"usage": {"prompt_tokens": 16000, "completion_tokens": 900},
                    "timings": {"prompt_n": 16000, "prompt_ms": 20000.0, "predicted_n": 900, "predicted_ms": 45000.0}}
        self.assertEqual(check.rates(response), {"input_tokens": 16000, "output_tokens": 900,
                         "prefill_tokens_per_second": 800.0, "generation_tokens_per_second": 20.0})
        missing = check.rates({"usage": {"prompt_tokens": 10}, "timings": {"prompt_n": 10, "prompt_ms": 0}})
        self.assertIsNone(missing["prefill_tokens_per_second"])
        self.assertIsNone(missing["generation_tokens_per_second"])

    def test_case_status_and_which_results_let_the_next_case_run(self):
        self.assertEqual(check.case_status({"patch": {"changed_files": []}}), "patch-submitted")
        self.assertEqual(check.case_status({"finish_reason": "length", "patch": {}}), "context-window-exhausted")
        self.assertEqual(check.case_status({"submission_error": "no tool call"}), "invalid-submission")
        self.assertEqual(check.case_status({"failure": {"kind": "memory-failure"}}), "memory-failure")
        self.assertTrue(check.keep_going({"status": "invalid-submission", "safe_to_cleanup": True}))
        self.assertFalse(check.keep_going({"status": "patch-submitted", "safe_to_cleanup": False}))
        self.assertFalse(check.keep_going({"status": "memory-failure", "safe_to_cleanup": True}))

    def test_patch_check_records_a_diff_inside_the_edit_scope_and_runs_nothing(self):
        base = check.unpack(self.root / "tasks/flask-coding/flask.tar.gz", self.root / "fixture")
        case = self.cases[0]
        path = case["edit_scope"]["existing_files"][0]
        original = (base / path).read_text()
        line = next(line for line in original.splitlines() if line.strip())
        arguments = {"explanation": "x", "edits": [{"path": path, "old_text": line, "new_text": line + "  # changed"}], "new_files": []}
        response = {"message": {"tool_calls": [{"function": {"name": "submit_patch", "arguments": json.dumps(arguments)}}]}}
        checked = check.patch_check(response, base, case["edit_scope"])
        self.assertEqual(checked["changed_files"], [path])
        self.assertIn("+" + line + "  # changed", checked["diff"])
        self.assertEqual((base / path).read_text(), original)
        with self.assertRaises(InvalidPatch):
            check.patch_check({"message": {"content": "I would change it", "tool_calls": []}}, base, case["edit_scope"])

    def test_changed_task_files_are_refused(self):
        cases = self.root / "tasks/flask-coding/cases.json"
        cases.write_bytes(cases.read_bytes().replace(b"Flask", b"Flusk", 1))
        with self.assertRaisesRegex(Refusal, "cases differ"):
            check.load_protocol(self.root)


class RunTest(RepositoryTest):
    def harness(self, fake=None, rows=(), answer="yes"):
        harness = Harness(self.root, fake or FakeTemper(), rows, answer)
        harness.lines = []
        harness.say = harness.lines.append
        return harness

    def run_json(self):
        (path,) = self.root.glob("runs/*/run.json")
        return json.loads(path.read_bytes()), path.parent

    def test_preview_creates_no_run_and_reports_the_default(self):
        harness = self.harness()
        self.assertEqual(harness.preview(None), 0)
        self.assertFalse((self.root / "runs").exists())
        text = "\n".join(harness.lines)
        self.assertIn("→ qwen3.8-27b-q4xl-mtp", text)
        self.assertIn("this Mac allows 24.0 GiB", text)  # zeta-new: 30 GiB
        self.assertIn("already in your Hugging Face cache", text)

    def test_declined_consent_removes_the_run_folder(self):
        harness = self.harness(answer="no")
        self.assertEqual(harness.start(None), 0)
        self.assertEqual(list((self.root / "runs").iterdir()), [])
        self.assertIn("Cancelled", "\n".join(harness.lines))

    def test_blocked_explicit_preset_leaves_no_run(self):
        harness = self.harness(FakeTemper(can_prepare={"qwen3.8-27b-q4xl-splash": False}))
        with self.assertRaisesRegex(Refusal, "needs Apple M3"):
            harness.start("qwen3.8-27b-q4xl-splash")
        self.assertEqual(list((self.root / "runs").iterdir()), [])

    def test_completed_run_cleans_up_and_writes_its_report(self):
        fake = FakeTemper()
        harness = self.harness(fake)
        self.assertEqual(harness.start(None), 0)
        run, directory = self.run_json()
        self.assertEqual((run["state"], run["cleanup"], run["cache"]["mode"]), ("complete", "complete", "shared"))
        self.assertEqual(harness.measured, ["async-stream", "template-decorators"])
        self.assertFalse((directory / "temper").exists())
        self.assertFalse((directory / "fixture").exists())
        self.assertTrue((directory / "execution.lock.json").is_file())
        result = json.loads((directory / "result.json").read_bytes())
        self.assertEqual(result["schema"], "field-kit-basic-check-result/v1")
        self.assertEqual(result["catalog"], ACTIVE)
        self.assertIn("| async-stream | patch-submitted", (directory / "report.md").read_text())
        prepare = next(environment for arguments, environment in fake.calls if arguments[:2] == ["execution", "prepare"])
        self.assertNotIn("hf-cache", prepare.get("HF_HUB_CACHE", ""))

    def test_missing_model_uses_a_private_cache_that_cleanup_removes(self):
        fake = FakeTemper(cached=False)
        harness = self.harness(fake)
        self.assertEqual(harness.start(None), 0)
        run, directory = self.run_json()
        self.assertEqual(run["cache"]["mode"], "private")
        self.assertEqual(run["plan"]["remaining_download_bytes"], 17 * GIB + 28000)
        prepare = next(environment for arguments, environment in fake.calls if arguments[:2] == ["execution", "prepare"])
        self.assertEqual(prepare["HF_HUB_CACHE"], str(directory / "hf-cache"))
        self.assertFalse((directory / "hf-cache").exists())

    def test_interrupted_case_is_kept_and_resume_runs_only_the_rest(self):
        harness = self.harness(rows=[{"status": "interrupted", "failure": {"kind": "interrupted"}}])
        self.assertEqual(harness.start(None), 1)
        run, directory = self.run_json()
        self.assertEqual(run["state"], "measuring")
        self.assertTrue((directory / "temper").exists())
        resumed = self.harness()
        self.assertEqual(resumed.start("qwen3.8-27b-q4xl-splash"), 0)  # an unfinished run comes first
        self.assertEqual(resumed.measured, ["template-decorators"])
        run, _ = self.run_json()
        self.assertEqual([row["status"] for row in run["cases"]], ["interrupted", "patch-submitted"])
        self.assertEqual(run["state"], "complete")

    def test_machine_side_stop_ends_the_run_but_still_cleans_up(self):
        harness = self.harness(rows=[{"status": "memory-failure", "failure": {"kind": "memory-failure"}}])
        self.assertEqual(harness.start(None), 0)
        run, _ = self.run_json()
        self.assertEqual(harness.measured, ["async-stream"])
        self.assertEqual((run["state"], run["stopped"]), ("complete", "async-stream: memory-failure"))

    def test_unconfirmed_shutdown_keeps_everything_and_refuses_to_continue(self):
        harness = self.harness(rows=[{"safe_to_cleanup": False}])
        self.assertEqual(harness.start(None), 1)
        run, directory = self.run_json()
        self.assertEqual((run["state"], run["cleanup"]), ("retained", "retained"))
        self.assertTrue((directory / "temper").exists())
        with self.assertRaisesRegex(Refusal, "could not confirm"):
            self.harness().start(None)

    def test_changed_checkout_refuses_to_continue_a_run(self):
        harness = self.harness(rows=[{"status": "interrupted", "failure": {"kind": "interrupted"}}])
        harness.start(None)
        run, directory = self.run_json()
        run["field_kit"]["sha256"] = "0" * 64
        (directory / "run.json").write_text(json.dumps(run))
        with self.assertRaisesRegex(Refusal, "checkout changed"):
            self.harness().start(None)


if __name__ == "__main__":
    unittest.main()
