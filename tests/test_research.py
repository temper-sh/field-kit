"""Behavioral checks of the rebuild, including the failures seen by testers."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fieldkit_runtime import execution, jobs, workloads
from fieldkit_runtime.common import Refusal, canonical_json, load_json, digest
from fieldkit_runtime.export import export_study
from fieldkit_runtime.material import validate_variant
from fieldkit_runtime.process import atomic_write, file_digest, run_process_progress
from fieldkit_runtime.study import Study, preview
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
        root.mkdir(parents=True, exist_ok=True)
        (root / "installed").write_text("owned")
        return {"generation": "c" * 64, "execution": self.inspect(path)}
    def remove(self, path, root, installation):
        return {"removed": True}

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
        self.temper = FakeTemper()
        self.study = Study(self.root, "research")
        self.study.initialize(preview(self.temper, [self.source], self.brief, hours=1, download_gib=1))
        self.addCleanup(patch.stopall)
        patch("fieldkit_runtime.study.Temper", FakeTemper).start()
        patch("fieldkit_runtime.execution.Temper", FakeTemper).start()
        patch("fieldkit_runtime.execution.sys_platform_macos", return_value=False).start()

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
        with patch.object(execution, "ManagedProbe", FakeProbe), patch.object(execution, "monitored_call", side_effect=call), \
             patch.object(execution.watcher, "sample_macos", return_value={"wired_bytes": 20 * GIB, "roles": {}}):
            return execution.execute(self.study, directory)

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
            execution.execute(self.study, directory)
        row = load_json(directory / "result.json")
        self.assertEqual(row["state"], "failed")
        self.assertIn("small setup defect", (directory / "failure.md").read_text())
        self.assertNotEqual(self.reserve(), directory)

    def test_finished_task_uses_existing_command_and_preserves_nonzero_outcome(self):
        script = self.root / "harness.py"
        script.write_text("import os, pathlib, sys\np=pathlib.Path(os.environ['FIELD_KIT_OUTPUT_DIR'])/'candidate'\np.mkdir()\n(p/'tests.log').write_text('1 failed')\nprint(os.environ['FIELD_KIT_MODEL'])\nsys.exit(1)\n")
        self.workload.write_bytes(canonical_json({"question": "Evaluate a finished task", "command": [sys.executable, str(script)],
                                                  "cwd": str(self.root)}))
        directory = self.reserve()
        with patch.object(execution, "ManagedProbe", FakeProbe), patch.object(execution, "monitored_call", return_value={}), \
             patch.object(execution.watcher, "sample_macos", return_value={"wired_bytes": 20 * GIB, "roles": {}}):
            execution.execute(self.study, directory)
        row = load_json(directory / "result.json", 64 * 1024**2)
        self.assertEqual(row["sessions"][0]["command"]["returncode"], 1)
        self.assertEqual(row["sessions"][0]["memory"]["engine_peak_wired_bytes"], 20 * GIB)
        self.assertEqual((directory / "sessions/task/output/candidate/tests.log").read_text(), "1 failed")
        self.assertEqual((directory / "sessions/task/stdout.txt").read_text().strip(), "research-model")
        self.reserve()

    def test_close_is_repeatable_and_preserves_user_and_evidence_material(self):
        directory = self.reserve()
        self.finish(directory)
        owned = self.study.runtime("research-model")
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
        with patch.object(execution, "ManagedProbe", FakeProbe), patch.object(execution, "monitored_call", return_value={}), \
             patch.object(execution.watcher, "sample_macos", return_value={"wired_bytes": 20 * GIB, "roles": {}}):
            execution.execute(self.study, directory)
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
            from fieldkit_runtime import cli, execution, study
            from tests.test_research import FakeTemper, FakeProbe, GIB
            cli.REPOSITORY = Path({str(self.root)!r})
            study.Temper = execution.Temper = FakeTemper
            execution.ManagedProbe = FakeProbe
            execution.monitored_call = lambda *args, **kwargs: {{}}
            execution.watcher.sample_macos = lambda *args: {{"wired_bytes": 20 * GIB, "roles": {{}}}}
            execution.sys_platform_macos = lambda: False
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
        self.assertEqual(memory_record(None, {"samples": 2, "wired_growth_bytes": 0}, None)["engine_peak_wired_bytes"], 0)

    def test_semantic_json_and_real_tool_calls(self):
        self.assertTrue(workloads.correct({"message": {"content": ' [1, 2] '}}, {"json": [1, 2]}))
        self.assertFalse(workloads.correct({"message": {"content": 'record_answer(...)'}},
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

    def test_launcher_help_uses_installed_python_without_setup(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "field-kit"
            executable.write_bytes((root / "field-kit").read_bytes())
            executable.chmod(0o755)
            (Path(temp) / "fieldkit_runtime").symlink_to(root / "fieldkit_runtime", target_is_directory=True)
            result = subprocess.run([str(executable), "--help"], capture_output=True, text=True,
                                    env={**os.environ, "FIELD_KIT_PYTHON": sys.executable})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(temp) / ".local").exists())
            self.assertIn("doctor", result.stdout)
