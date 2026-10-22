"""Behavioral checks of the rebuild, including the failures seen by testers."""
import copy
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fieldkit_runtime import attempt, jobs, workloads
from fieldkit_runtime.adapters.interfaces import chat
from fieldkit_runtime.approval import preview
from fieldkit_runtime.context import Context
from fieldkit_runtime.adapters import engines, models
from fieldkit_runtime.adapters.engines.base import EngineAdapter
from fieldkit_runtime.common import Refusal, canonical_json, load_json, digest
from fieldkit_runtime.export import export_study
from fieldkit_runtime.material import validate_variant
from fieldkit_runtime.process import CommandFailure, CommandResult, atomic_write, file_digest, run_process_progress
from fieldkit_runtime.study import Study
from fieldkit_runtime.temper import version_at_least

GIB = 1024**3
FACTS = {"schema": "temper-machine-facts/v1", "chip": "Test chip", "physical_memory_bytes": 64 * GIB,
         "wired_limit_mib": 50000, "target": {"os": "darwin", "arch": "arm64"}}

def lock(name="research-model"):
    return {"schema": "temper-execution-lock/v1", "preset": name, "target": FACTS["target"],
        "records": {"schema": "temper-catalog/v1", "date": "2026-10-21", "runtime": {},
            "artifacts": {"weights": {"files": [{"path": "model.gguf", "bytes": 1024, "sha256": "a" * 64}]}},
            "templates": {"template": {"files": [{"path": "template.jinja", "bytes": 50, "sha256": "b" * 64}]}},
            "engines": {"engine": {"adapter": "llama-server/v1", "release": "fixed"}},
            "presets": {name: {"engine": "engine", "artifact": "weights", "template": "template",
                "interface": "chat-completions", "request_defaults": {"reasoning": "on"},
                "profiles": [{"min_memory_gib": 8, "context_window_tokens": 4096,
                              "engine_config": {"kind": "llama-server/v1", "kv_cache": "q8"}}]}}}}

class FakeTemper:
    """Shared fake state: Field Kit constructs a new Temper for each operation."""
    layout = {"id": None, "busy": False, "fail_activate": False}
    calls = []
    check = {"valid": True, "refusals": [], "memory_bytes": GIB, "model_bytes": GIB}
    plan = {"remaining_download_bytes": 0, "remaining_disk_bytes": 0, "free_disk_bytes": 100 * GIB, "can_prepare": True}
    plan_supported = True

    @classmethod
    def reset(cls):
        cls.layout = {"id": None, "busy": False, "fail_activate": False}
        cls.calls = []
        cls.check = {"valid": True, "refusals": [], "memory_bytes": GIB, "model_bytes": GIB}
        cls.plan_supported = True

    def __init__(self, path=Path(sys.executable)):
        self.path = Path(path).resolve()
    def identity(self):
        return {"path": str(self.path), "version": "temper 0.3.0", "sha256": "e" * 64}
    def machine_facts(self):
        return copy.deepcopy(FACTS)
    def inspect(self, path):
        preset = load_json(path)["preset"]
        return {"schema": "temper-execution/v1", "preset": preset, "presets": [preset]}
    def prepare(self, path, root, installation, timeout, environment):
        FakeTemper.calls.append(("prepare", installation, environment.get("HF_HUB_CACHE"), environment.get("HF_HOME")))
        root.mkdir(parents=True, exist_ok=True)
        (root / "installed").write_text("owned")
        return {"generation": "c" * 64, "execution": self.inspect(path)}
    def remove(self, path, root, installation):
        FakeTemper.calls.append(("remove", installation))
        return {"removed": True}
    def supports(self, command):
        return command != ["preset", "plan"] or FakeTemper.plan_supported
    def check_preset(self, preset, root, environment):
        FakeTemper.calls.append(("check", preset, str(root), environment.get("HF_HUB_CACHE")))
        return {"schema": "temper-preset-check/v1", "presets": [{"id": preset, **FakeTemper.check}]}
    def plan_preset(self, preset, root, environment):
        FakeTemper.calls.append(("plan", preset, str(root), environment.get("HF_HUB_CACHE")))
        return {"schema": "temper-preset-plan/v1", "preset": preset, "plan": dict(FakeTemper.plan)}
    def layout_status(self, root=None):
        FakeTemper.calls.append(("status", root))
        layout = FakeTemper.layout["id"]
        job = {"layout": layout, "name": "Default", "listen": "127.0.0.1:8080"}
        return {"state": {"schema": "temper-activation/v1", **({"desired": job, "current": job} if layout else {})},
                "observed": {"exists": bool(layout), "ready": bool(layout), "busy": FakeTemper.layout["busy"]},
                "pending": False}
    def layout_stop(self, root=None):
        FakeTemper.calls.append(("stop", root))
        if FakeTemper.layout["busy"]:
            raise CommandFailure(["temper"], CommandResult(b"", b"active work prevents this transition", 1))
        FakeTemper.layout["id"] = None
        return {}
    def layout_activate(self, layout, root=None):
        FakeTemper.calls.append(("activate", layout, root))
        if FakeTemper.layout["fail_activate"]:
            raise CommandFailure(["temper"], CommandResult(b"", b"activation pending", 1))
        FakeTemper.layout["id"] = layout
        return {}

def fake_context(call=None, wired=20 * GIB):
    """The machine as tests see it: fake Temper and probe, scripted HTTP, ample APFS disk."""
    return Context(temper=FakeTemper, probe=FakeProbe, local_probe=FakeProbe, call=call or (lambda *args, **kwargs: {}),
                   sample=lambda binding, timeout: {"wired_bytes": wired, "roles": {}}, volume=fake_volume,
                   power=lambda: {"counter": "pmset", "source": "AC Power", "battery_percent": 80,
                                  "low_power_mode": False, "power_mode": None}, keep_awake=False)

def fake_volume(path):
    """An internal APFS volume with ample space; real volume facts have their own test."""
    return {"path": str(Path(path).absolute()), "exists": Path(path).exists(), "mount_point": "/", "filesystem": "apfs",
            "device": "/dev/test", "internal": True, "protocol": "Apple Fabric", "free_bytes": 500 * GIB}

class FakeProbe:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.host = "127.0.0.1"
        self.port = int(self.listen.rsplit(":", 1)[1])
        self.full_binding = {}
    def start(self): pass
    def observe_engine(self): return True
    def ensure_healthy(self): pass
    def validate_owned_boundary(self): pass
    def finish(self):
        return {"safe_to_cleanup": True, "samples": 5, "wired_baseline_bytes": GIB,
                "wired_growth_bytes": 20 * GIB, "roles": {}, "issues": [], "stop_reasons": [],
                "swap_growth_bytes": 0, "max_gap_milliseconds": 5000}

class StudyTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "candidate.lock.json"
        self.source.write_bytes(canonical_json(lock()))
        self.brief = self.root / "brief.md"
        self.brief.write_text("Find useful presets; compare speed, quality and memory.")
        self.workload = self.root / "workload.json"
        self.workload.write_bytes(canonical_json(workloads.generate("smoke")))
        self.cache = self.root / "hf-cache"
        self.cache.mkdir()
        FakeTemper.reset()
        self.addCleanup(patch.stopall)
        self.temper = FakeTemper()
        self.study = Study(self.root, "research", fake_context())
        self.study.initialize(preview(self.temper, [self.source], self.brief, hours=1, download_gib=1,
                                      weights_cache=self.cache, context=self.study.context), self.temper)

    def run_attempt(self, directory, call=None, wired=20 * GIB, study=None):
        study = study or self.study
        study.context = fake_context(call, wired)
        return attempt.execute(study, directory)

    def reserve(self, path=None, variant=None):
        return self.study.reserve("research-model", path or self.workload, "Answer the next useful question", variant)

    def finish(self, directory, **changes):
        row = load_json(directory / "run.json", 64 * 1024**2)
        row.update(state="complete", safe_to_cleanup=True, **changes)
        atomic_write(directory / "run.json", canonical_json(row))
        atomic_write(directory / "result.json", canonical_json(row))
        return row

    def execute_requests(self, directory, *, content="wrong answer", finish="length"):
        def call(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            stream = kwargs["stream_reader"]
            stream.raw_path.write_text("preserved raw response")
            return {"message": {"content": content, "reasoning": "", "tool_calls": []},
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 20},
                    "timings": {"prompt_n": 1000, "prompt_ms": 100, "predicted_n": 20, "predicted_ms": 200},
                    "service_seconds": 0.3, "first_token_seconds": 0.1, "first_answer_seconds": 0.1,
                    "finish_reason": finish, "stream_complete": True}
        return self.run_attempt(directory, call)

    def test_installed_temper_and_any_model_need_no_coding_evaluator(self):
        self.assertTrue(version_at_least("temper 0.3.0"))
        self.assertFalse(version_at_least("temper 0.2.0"))
        self.assertNotIn("evaluator", self.study.load())
        directory = self.reserve()
        self.assertEqual(load_json(directory / "run.json")["candidate"], "research-model")

    def test_execution_keeps_versioned_study_sources_unchanged(self):
        sources = self.root / "studies"
        sources.mkdir(exist_ok=True)
        brief = sources / "research.md"
        brief.write_text("Maintained study question.")
        directory = self.reserve()
        self.execute_requests(directory)
        self.study.close()
        self.assertEqual(list(sources.iterdir()), [brief])
        self.assertEqual(brief.read_text(), "Maintained study question.")
        self.assertTrue((directory / "result.json").is_file())

    def test_symlinked_work_directory_cannot_redirect_study_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = Path(temp)
            (repository / ".work").symlink_to(self.root, target_is_directory=True)
            with self.assertRaisesRegex(Refusal, "symlinks"):
                Study(repository, "redirected")
            self.assertFalse((self.root / "studies" / "redirected").exists())

    def test_wrong_truncated_answer_keeps_rates_memory_and_allows_cache_trial(self):
        directory = self.reserve()
        self.execute_requests(directory)
        result = load_json(directory / "result.json", 64 * 1024**2)
        session = result["sessions"][0]
        self.assertFalse(session["steps"][0]["correct"])
        self.assertEqual(session["steps"][0]["finish_reason"], "length")
        self.assertEqual(session["steps"][0]["prefill_tokens_per_second"], 10000)
        self.assertEqual(session["memory"]["engine_peak_wired_bytes"], 20 * GIB)
        self.assertEqual((session["power"]["source"], session["power"]["low_power_mode"]), ("AC Power", False))
        self.assertNotIn("measurement_valid", result)
        self.assertEqual(result["state"], "complete")
        cache = self.root / "cache.json"
        cache.write_bytes(canonical_json(workloads.generate("cache", records=8)))
        self.assertNotEqual(self.reserve(cache), directory)

    def test_all_cache_steps_run_after_wrong_answer_and_append_uses_actual_answer(self):
        path = self.root / "cache.json"
        path.write_bytes(canonical_json(workloads.generate("cache", records=8)))
        directory = self.reserve(path)
        self.execute_requests(directory, content='["wrong"]', finish="stop")
        row = load_json(directory / "result.json", 64 * 1024**2)["sessions"][0]
        self.assertEqual(len(row["steps"]), 3)
        request = load_json(directory / "sessions/session/append/request.json")
        self.assertEqual(request["messages"][-2]["content"], '["wrong"]')
        self.assertTrue(all(step["correct"] is False for step in row["steps"]))

    def test_transport_failure_ends_the_session_and_keeps_the_partial_stream(self):
        self.workload.write_bytes(canonical_json({"question": "Stop after a broken request", "steps": [
            {"id": name, "request": {"messages": [{"role": "user", "content": "Answer"}]}} for name in ("first", "second")]}))
        # The approved request deadline, and a stalled socket read inside it.
        for error in (TimeoutError("request deadline"), socket.timeout("timed out")):
            with self.subTest(error=type(error).__name__):
                directory = self.reserve()
                sent = []
                def call(probe, path, payload, timeout, **kwargs):
                    if kwargs.get("method") == "GET":
                        return {}
                    sent.append(path)
                    kwargs["stream_reader"].raw_path.write_text("data: partial")
                    raise error
                self.run_attempt(directory, call)
                result = load_json(directory / "result.json", 64 * 1024**2)
                self.assertEqual(len(sent), 1)
                self.assertEqual(result["sessions"][0]["failure"]["kind"], "timeout")
                self.assertEqual((result["state"], result["error"]["kind"]), ("failed", "timeout"))
                self.assertEqual((directory / "sessions/session/first/response.sse").read_text(), "data: partial")
                self.assertTrue((directory / "sessions/session/first/response.json").is_file())
                self.assertTrue(result["safe_to_cleanup"])

    def test_new_engine_adapter_runs_without_a_core_executor_change(self):
        class FixtureObserver:
            def capture(self, phase): pass
            def poll(self): pass
            def measurements(self): return {"fixture_native": {"peak_bytes": 1234}}
        class FixtureEngine(EngineAdapter):
            health_path = "/ready-for-work"
            helper_limits = {"router": GIB, "helper": 3 * GIB}
            def metrics(self, response):
                return {**super().metrics(response), "generation_tokens_per_second": 7}
            def memory_observer(self, read, native_prefix):
                prefixes.append(native_prefix)
                return FixtureObserver()
        candidate = lock("brand-new-model")
        candidate["records"]["engines"]["engine"]["adapter"] = "fixture/v1"
        self.source.write_bytes(canonical_json(candidate))
        with patch.dict(engines.ADAPTERS, {"fixture/v1": FixtureEngine}):
            study = Study(self.root, "new-engine", fake_context())
            study.initialize(preview(self.temper, [self.source], self.brief, hours=1, download_gib=1,
                                     weights_cache=self.cache, context=study.context))
            directory = study.reserve("brand-new-model", self.workload, "Exercise adapter integration")
            seen, prefixes = [], []
            def call(probe, path, payload, timeout, **kwargs):
                seen.append(path)
                if kwargs.get("method") == "GET":
                    return {}
                return {"message": {"content": "FIELD_KIT_OK"}, "stream_complete": True, "finish_reason": "stop"}
            self.run_attempt(directory, call, GIB, study)
            result = load_json(directory / "result.json")
            self.assertEqual(result["engine_adapter"], "fixture/v1")
            self.assertEqual(result["model_adapter"], models.DEFAULT)
            self.assertEqual(seen, ["/upstream/brand-new-model/ready-for-work", "/v1/chat/completions"])
            self.assertEqual(prefixes, ["/upstream/brand-new-model"])
            session = result["sessions"][0]
            self.assertEqual(session["steps"][0]["generation_tokens_per_second"], 7)
            self.assertTrue(session["steps"][0]["correct"])
            self.assertEqual(session["memory"]["fixture_native"]["peak_bytes"], 1234)
            self.assertTrue(result["safe_to_cleanup"])

    def test_flint_replay_preserves_reasoning_and_rejected_call_identity(self):
        call = {"id": "first-call", "type": "function", "function": {"name": "repair", "arguments": "{}"}}
        work = {"question": "Continue after an unsuccessful tool call", "model_adapter": "overridden/v1", "steps": [
            {"id": "first", "request": {"messages": [{"role": "user", "content": "Repair this function"}]}},
            {"id": "feedback", "append_previous": True, "request": {"messages": [
                {"role": "tool", "tool_call_id": "first-call", "content": "Missing required edits"}]}}]}
        self.workload.write_bytes(canonical_json(work))
        directory = self.study.reserve("research-model", self.workload, "Check Flint replay", model_adapter="flint/v1")
        seen = []
        def request(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            seen.append(copy.deepcopy(payload))
            return {"message": {"content": "", "reasoning": "Keep this thought", "tool_calls": [call]},
                    "finish_reason": "tool_calls", "stream_complete": True}
        self.run_attempt(directory, request, GIB)
        result = load_json(directory / "result.json")
        self.assertEqual(result["model_adapter"], "flint/v1")
        self.assertEqual(load_json(directory / "workload.json")["model_adapter"], "flint/v1")
        replay, feedback = seen[1]["messages"][-2:]
        self.assertEqual(replay["reasoning_content"], "Keep this thought")
        self.assertEqual(replay["tool_calls"], [call])
        self.assertEqual(feedback["tool_call_id"], call["id"])
        self.assertTrue(result["safe_to_cleanup"])

    def test_bad_format_does_not_erase_metrics_or_block_an_independent_step(self):
        work = {"question": "Keep format failures separate from performance", "model_adapter": "flint/v1", "steps": [
            {"id": name, "request": {"messages": [{"role": "user", "content": "Return an answer"}]},
             "expect": {"text": "OK"}} for name in ("first", "independent")]}
        self.workload.write_bytes(canonical_json(work))
        directory = self.reserve()
        self.execute_requests(directory, content="<tool_call>unfinished", finish="stop")
        result = load_json(directory / "result.json")
        steps = result["sessions"][0]["steps"]
        self.assertEqual(len(steps), 2)
        for step in steps:
            self.assertEqual(step["model_format"]["state"], "invalid")
            self.assertIsNone(step["correct"])
            self.assertIsNone(step["failure"])
            self.assertEqual(step["generation_tokens_per_second"], 100)
        self.assertTrue(result["safe_to_cleanup"])

    def test_unknown_adapter_refused_before_reserving_or_preparing_a_trial(self):
        with self.assertRaisesRegex(Refusal, "model adapter"):
            self.study.reserve("research-model", self.workload, "Invalid selection", model_adapter="unknown/v1")
        self.assertEqual(self.study.load()["runs"], [])
        candidate = lock()
        candidate["records"]["engines"]["engine"]["adapter"] = "llama-server/v999"
        self.source.write_bytes(canonical_json(candidate))
        with self.assertRaisesRegex(Refusal, "engine adapter"):
            preview(self.temper, [self.source], self.brief, weights_cache=self.cache, context=self.study.context)

    def test_settings_variant_allowed_but_new_weights_and_template_are_not(self):
        original = lock()
        variant = copy.deepcopy(original)
        variant["records"]["presets"]["research-model"]["profiles"][0]["context_window_tokens"] = 16384
        validate_variant(original, variant, 32768)
        for key in ("artifacts", "templates"):
            changed = copy.deepcopy(variant)
            record = next(iter(changed["records"][key].values()))
            record["files"][0]["sha256"] = "f" * 64
            with self.assertRaises(Refusal):
                validate_variant(original, changed, 32768)
        with self.assertRaisesRegex(Refusal, "context"):
            validate_variant(original, variant, 8192)

    def test_unreviewed_output_and_repaired_runtime_do_not_block_new_attempt(self):
        self.finish(self.reserve())
        with patch("fieldkit_runtime.study.runtime_identity", return_value={"sha256": "new-repaired-runtime"}):
            directory = self.reserve()
        self.assertEqual(load_json(directory / "run.json")["field_kit"]["sha256"], "new-repaired-runtime")

    def test_active_or_unknown_process_blocks_effects_but_not_on_quality(self):
        first = self.reserve()
        with self.assertRaisesRegex(Refusal, "active"):
            self.reserve()
        row = load_json(first / "run.json")
        row.update(state="retained", safe_to_cleanup=False)
        atomic_write(first / "run.json", canonical_json(row))
        with self.assertRaisesRegex(Refusal, "shutdown"):
            self.study.close()
        self.assertEqual(self.study.load()["state"], "open")

    def test_recover_dead_worker_preserves_attempt_and_allows_new_trial(self):
        first = self.reserve()
        row = load_json(first / "run.json")
        row.update(state="preparing", worker={"pid": 9999999, "started": "gone"})
        atomic_write(first / "run.json", canonical_json(row))
        self.assertEqual(self.study.recover()["recovered"], [first.name])
        self.assertEqual(load_json(first / "run.json")["state"], "interrupted")
        self.assertNotEqual(self.reserve(), first)

    def test_recovery_requires_matching_stopped_temper_proof(self):
        directory = self.reserve()
        row = load_json(directory / "run.json")
        row.update(state="retained", safe_to_cleanup=False, worker={"pid": 9999999, "started": "gone"})
        atomic_write(directory / "run.json", canonical_json(row))
        stage = directory / "sessions/test"
        intent = {"root": "/owned/root", "installation": "field-kit", "generation": "c" * 64, "listen": "127.0.0.1:12345"}
        atomic_write(stage / "intent.json", canonical_json(intent))
        with self.assertRaisesRegex(Refusal, "uncertain"):
            self.study.recover()
        status = {**intent, "schema": "temper-probe-status/v1", "state": "stopped", "safe_to_cleanup": True}
        atomic_write(stage / "process/temper-status.json", canonical_json({**status, "generation": "d" * 64}))
        with self.assertRaisesRegex(Refusal, "confirmed"):
            self.study.recover()
        atomic_write(stage / "process/temper-status.json", canonical_json(status))
        self.assertEqual(self.study.recover()["recovered"], [directory.name])

    def test_preparation_failure_is_retained_and_retry_is_a_new_attempt(self):
        directory = self.reserve()
        with patch.object(FakeTemper, "prepare", side_effect=Refusal("small setup defect")):
            attempt.execute(self.study, directory)
        row = load_json(directory / "result.json")
        self.assertEqual(row["state"], "failed")
        self.assertIn("small setup defect", (directory / "failure.md").read_text())
        self.assertNotEqual(self.reserve(), directory)

    def test_finished_task_uses_existing_command_and_preserves_nonzero_outcome(self):
        script = self.root / "harness.py"
        script.write_text("import os, pathlib, sys\np=pathlib.Path(os.environ['FIELD_KIT_OUTPUT_DIR'])/'candidate'\np.mkdir()\n(p/'tests.log').write_text('1 failed')\n(p/'adapter.txt').write_text(os.environ['FIELD_KIT_MODEL_ADAPTER'])\n(p/'native.txt').write_text(os.environ['FIELD_KIT_NATIVE_URL'])\n(p/'window.txt').write_text(os.environ['FIELD_KIT_CONTEXT_WINDOW_TOKENS'])\nprint(os.environ['FIELD_KIT_MODEL'])\nsys.exit(1)\n")
        self.workload.write_bytes(canonical_json({"question": "Evaluate a finished task", "model_adapter": "flint/v1", "command": ["{python}", str(script)],
                                                  "cwd": str(self.root)}))
        directory = self.reserve()
        self.run_attempt(directory)
        row = load_json(directory / "result.json", 64 * 1024**2)
        self.assertEqual(row["sessions"][0]["command"]["returncode"], 1)
        self.assertEqual(row["sessions"][0]["memory"]["engine_peak_wired_bytes"], 20 * GIB)
        self.assertEqual((directory / "sessions/task/output/candidate/tests.log").read_text(), "1 failed")
        self.assertEqual((directory / "sessions/task/stdout.txt").read_text().strip(), "research-model")
        self.assertEqual((directory / "sessions/task/output/candidate/adapter.txt").read_text(), "flint/v1")
        self.assertRegex((directory / "sessions/task/output/candidate/native.txt").read_text(),
                         r"^http://127\.0\.0\.1:\d+/upstream/research-model$")
        self.assertEqual((directory / "sessions/task/output/candidate/window.txt").read_text(), "4096")
        self.assertEqual(row["sessions"][0]["command"]["argv"][0], sys.executable)
        self.assertEqual(load_json(directory / "workload.json")["command"][0], "{python}")
        self.assertEqual(row["model_adapter_scope"], "external-harness")
        self.reserve()

    def test_close_is_repeatable_and_preserves_user_and_evidence_material(self):
        directory = self.reserve()
        self.finish(directory)
        owned = self.study.runtime()
        owned.mkdir(parents=True)
        (owned / "weight").write_text("temporary")
        external = self.root / "user-cache"
        external.mkdir()
        (external / "weight").write_text("user")
        self.study.close()
        self.study.close()
        self.assertFalse(owned.exists())
        self.assertEqual((external / "weight").read_text(), "user")
        self.assertTrue((directory / "result.json").is_file())

    def test_missing_harness_binary_is_repairable_without_uncertain_process_state(self):
        self.workload.write_bytes(canonical_json({"question": "Run existing harness", "cwd": str(self.root),
            "command": [str(self.root / "missing-harness")]}))
        directory = self.reserve()
        self.run_attempt(directory)
        self.assertEqual(load_json(directory / "sessions/task/command.json")["state"], "not-started")
        self.assertTrue(load_json(directory / "result.json")["safe_to_cleanup"])
        self.reserve()

    def test_detached_task_can_be_stopped_and_waited_on_before_export(self):
        import textwrap
        source = Path(__file__).resolve().parents[1]
        launcher = self.root / "field-kit"
        launcher.write_text("#!" + sys.executable + "\n" + textwrap.dedent(f'''\
            import sys
            from pathlib import Path
            sys.path.insert(0, {str(source)!r})
            from fieldkit_runtime import cli, context
            from tests.test_research import fake_context
            cli.REPOSITORY = Path({str(self.root)!r})
            context.DEFAULT = fake_context()
            sys.exit(cli.main())
            '''))
        launcher.chmod(0o755)
        ready = self.root / "harness-started"
        script = self.root / "long-task.py"
        script.write_text(f"from pathlib import Path\nimport time\nPath({str(ready)!r}).write_text('started')\ntime.sleep(30)\n")
        self.workload.write_bytes(canonical_json({"question": "Exercise detached shutdown", "cwd": str(self.root),
            "command": [sys.executable, str(script)]}))
        directory = self.reserve()
        worker = jobs.start(self.root, self.study.name, directory.name)
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and worker.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists(), (directory / "worker.log").read_text())
            jobs.stop(directory)
            result = jobs.wait(directory, 5)
            worker.wait(timeout=5)
            self.assertEqual(result["state"], "interrupted")
            self.assertFalse(result["running"])
            self.assertTrue(load_json(directory / "result.json")["safe_to_cleanup"])
            export_study(self.study, self.root / "interrupted.zip")
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=5)

    def test_export_timestamp_ids_candidate_logs_repair_patch_and_failed_attempt(self):
        import zipfile
        first = self.reserve()
        self.finish(first, error={"message": "known defect"})
        atomic_write(first / "review/tests/candidate/stdout.txt", b"candidate tests")
        atomic_write(first / "review/fixture/note.txt", b"necessary evidence")
        report = self.root / "repair.md"
        report.write_text("Expected export; failed on timestamp ID; repaired and verified.")
        diff = self.root / "repair.diff"
        diff.write_text("local patch")
        self.study.repair(report, diff)
        private = self.study.directory / ".runtime"
        private.mkdir()
        (private / "weights").write_text("not exported")
        target = self.root / "returned.zip"
        export_study(self.study, target)
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            self.assertTrue(any("review/tests/candidate/stdout.txt" in name for name in names))
            self.assertTrue(any("review/fixture/note.txt" in name for name in names))
            self.assertTrue(any(name.endswith("repair.diff") for name in names))
            self.assertFalse(any(".runtime" in name for name in names))
            manifest = json.loads(archive.read("research/export-manifest.json"))
            self.assertEqual(len(manifest), len(names) - 1)
            for name, record in manifest.items():
                self.assertEqual(digest(archive.read("research/" + name)), record["sha256"])
        original = target.read_bytes()
        with self.assertRaises(Refusal):
            export_study(self.study, target)
        self.assertEqual(target.read_bytes(), original)

    def test_export_missing_or_changed_inputs_is_actionable_and_does_not_rerun(self):
        directory = self.reserve()
        self.finish(directory)
        (directory / "execution.lock.json").write_text("{}")
        with self.assertRaisesRegex(Refusal, "changed"):
            export_study(self.study, self.root / "bad.zip")
        self.assertFalse((self.root / "bad.zip").exists())

class WorkloadAndJobTest(unittest.TestCase):
    def test_missing_memory_samples_are_not_reported_as_zero_usage(self):
        from fieldkit_runtime.measurements import memory_record
        self.assertIsNone(memory_record(None, {"samples": 0, "wired_growth_bytes": 0}, None)["engine_peak_wired_bytes"])
        loaded = {"wired_bytes": GIB, "roles": {}}
        self.assertEqual(memory_record(loaded, {"samples": 2, "wired_growth_bytes": 0, "wired_baseline_bytes": GIB},
                                       None)["engine_peak_wired_bytes"], 0)

    def test_peak_memory_is_never_below_the_loaded_sample(self):
        from fieldkit_runtime.measurements import memory_record
        loaded = {"wired_bytes": 25 * GIB, "roles": {"engine": {"rss_bytes": 9 * GIB, "current_footprint_bytes": 2 * GIB}}}
        resources = {"samples": 4, "wired_baseline_bytes": GIB, "wired_growth_bytes": 23 * GIB,
                     "roles": {"engine": {"rss_bytes_max": 7 * GIB, "peak_footprint_bytes_max": 3 * GIB}}}
        record = memory_record(loaded, resources, None)
        self.assertEqual((record["engine_loaded_wired_bytes"], record["engine_peak_wired_bytes"]), (24 * GIB, 24 * GIB))
        self.assertEqual(record["engine_rss_max_bytes"], 9 * GIB)
        self.assertEqual(record["engine_peak_footprint_bytes"], 3 * GIB)

    def test_a_serve_that_never_loaded_reports_no_peak(self):
        from fieldkit_runtime.measurements import memory_record
        resources = {"samples": 1, "wired_baseline_bytes": GIB, "wired_growth_bytes": 753664, "swap_growth_bytes": 0,
                     "roles": {"engine": {"rss_bytes_max": 25395200, "peak_footprint_bytes_max": 5374480}}}
        record = memory_record(None, resources, None)
        self.assertEqual([record[key] for key in ("engine_peak_wired_bytes", "engine_rss_max_bytes", "engine_peak_footprint_bytes")],
                         [None, None, None])
        self.assertIn("before it finished loading", record["not_observed"])
        self.assertEqual(record["peak_roles"], resources["roles"])
        self.assertEqual(record["swap_growth_bytes"], 0)

    def test_semantic_json_and_real_tool_calls(self):
        self.assertTrue(chat.correct({"message": {"content": ' [1, 2] '}}, {"json": [1, 2]}))
        self.assertFalse(chat.correct({"message": {"content": 'record_answer(...)'}},
                                          {"tool": {"name": "record_answer", "arguments": {"answer": "FIELD_KIT_OK"}}}))
        cache = workloads.generate("cache", records=8)
        steps = cache["sessions"][0]["steps"]
        self.assertNotEqual(steps[0]["expect"], steps[-1]["expect"])
        self.assertTrue(steps[1]["append_previous"])

    def test_engine_screen_keeps_work_matched_and_processes_separate(self):
        sessions = workloads.generate("engine", records=512)["sessions"]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["steps"][0]["expect"], sessions[1]["steps"][0]["expect"])
        self.assertLess(len(sessions[0]["steps"][0]["request"]["messages"][0]["content"]),
                        len(sessions[1]["steps"][0]["request"]["messages"][0]["content"]))

    def test_wait_blocks_in_tool_and_returns_completion_once(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            row = {"id": "run", "state": "measuring", "candidate": "x", "reason": "question",
                   "created_epoch": time.time(), "worker": jobs.process_identity(os.getpid())}
            atomic_write(directory / "run.json", canonical_json(row))
            def finish():
                time.sleep(0.15)
                atomic_write(directory / "run.json", canonical_json({**row, "state": "complete"}))
            thread = threading.Thread(target=finish)
            thread.start()
            result = jobs.wait(directory, 2)
            thread.join()
            self.assertEqual(result["state"], "complete")

    def test_reused_pid_is_never_signaled(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            atomic_write(directory / "run.json", canonical_json({"id": "run", "state": "measuring",
                "worker": {"pid": os.getpid(), "started": "different process"}}))
            with patch("fieldkit_runtime.jobs.os.kill") as kill:
                with self.assertRaisesRegex(Refusal, "reused"):
                    jobs.stop(directory)
                kill.assert_not_called()

    def test_command_timeout_retains_process_proof_and_stops_owned_children(self):
        with tempfile.TemporaryDirectory() as temp:
            identity = Path(temp) / "command.json"
            result = run_process_progress([sys.executable, "-c", "import time; time.sleep(10)"],
                                          0.15, identity_path=identity)
            self.assertEqual(result.returncode, 124)
            record = load_json(identity)
            self.assertEqual(record["state"], "exited")
            self.assertFalse(jobs.alive(record["process"]))
