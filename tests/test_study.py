"""Decision and failure-boundary tests; no downloads, servers or model calls."""
import contextlib
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from fieldkit_runtime import check, review, study, workloads
from fieldkit_runtime.common import Refusal, canonical_json
from fieldkit_runtime.process import atomic_write, file_digest
from tests.test_check import RepositoryTest, FakeTemper, FACTS, GIB
from fieldkit_runtime.temper import Temper

PRESET = "qwen3.8-27b-q4xl-mtp"


class WorkloadsTest(RepositoryTest):
    def test_smoke_requires_real_tool_call_and_does_no_coding(self):
        case = workloads.cases(self.cases, "smoke")[0]
        prose = {"message": {"content": 'record_answer({"answer":"FIELD_KIT_OK"})'}}
        self.assertFalse(workloads.correct(prose, case["oracle"])[0])
        tool = {"message": {"tool_calls": [{"function": {"name": "record_answer", "arguments": '{"answer":"FIELD_KIT_OK"}'}}]}}
        self.assertTrue(workloads.correct(tool, case["oracle"])[0])
        self.assertNotIn("edit_scope", case)

    def test_cache_uses_continuation_and_checks_changed_prefix_not_stale_answer(self):
        case = workloads.cases(self.cases, "cache", records=512)[0]
        cold, append, rewind = case["steps"]
        self.assertEqual(append["request"]["messages"][:2], cold["request"]["messages"])
        self.assertNotEqual(rewind["request"]["messages"][1], cold["request"]["messages"][1])
        stale = {"message": {"content": json.dumps(cold["oracle"]["answer"])}}
        self.assertTrue(workloads.correct(stale, cold["oracle"])[0])
        self.assertFalse(workloads.correct(stale, rewind["oracle"])[0])
        self.assertEqual(len(cold["request"]["messages"][1]["content"].splitlines()), 513)

    def test_mlx_topologies_and_health_are_explicit(self):
        for family, roles in (("rapid-mlx", ["crash-log", "engine", "router"]),
                              ("vllm-metal", ["engine", "frontend", "resource-tracker", "router"])):
            spec = check.watch_spec(self.protocol, FACTS, family)
            check.watcher.validate_watch_spec(spec)
            self.assertEqual([role["id"] for role in spec["roles"]], roles)
        self.assertEqual(workloads.health_path("rapid-mlx"), "/health/ready")

    def test_synthetic_session_uses_one_process_and_keeps_per_request_evidence(self):
        worker = check.BasicCheck(self.root, Mock(), workload="cache", records=16)
        case = worker.cases[0]
        responses = [{"message": {"content": json.dumps(step["oracle"]["answer"]), "reasoning": ""},
                      "usage": {"prompt_tokens": 200 + i, "completion_tokens": 20}, "service_seconds": 1,
                      "stream_complete": True, "finish_reason": "stop"} for i, step in enumerate(case["steps"])]
        run = {"preset": {"id": PRESET, "family": "rapid-mlx"}, "machine": FACTS,
               "preparation": {"generation": "a" * 64}}
        probe = Mock()
        probe.finish.return_value = {"safe_to_cleanup": True, "samples": 3, "wired_growth_bytes": 18 * GIB}
        with patch.object(check, "ManagedProbe", return_value=probe) as factory, \
                patch.object(check, "monitored_call") as health, \
                patch.object(check.watcher, "sample_macos", return_value={}), \
                patch.object(worker, "request", side_effect=responses) as request:
            row = worker.measure_case(self.root / "run", run, case)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(request.call_count, 3)
        self.assertIn("/health/ready", health.call_args.args[1])
        self.assertEqual(row["status"], "passed")
        self.assertEqual([step["input_tokens"] for step in row["steps"]], [200, 201, 202])
        self.assertEqual(row["task_seconds"], 3)
        probe.finish.assert_called_once()

    def test_uncommitted_measurement_never_starts_a_replacement_engine(self):
        worker = check.BasicCheck(self.root, Mock())
        (self.root / "run/cases/smoke").mkdir(parents=True)
        run = {"preset": {"id": PRESET, "family": "llama-server"}}
        with patch.object(check, "ManagedProbe") as factory:
            row = worker.measure_case(self.root / "run", run, worker.cases[0])
        self.assertFalse(row["safe_to_cleanup"])
        factory.assert_not_called()

    def test_declared_qwen_copy_can_use_another_temper_engine(self):
        temper = Mock()
        temper.preset_check.return_value = {"id": "qwen-rapid", "name": "Qwen Rapid", "form": "copy", "changes": []}
        temper.preset_plan.return_value = {"can_prepare": True, "presets": {"qwen-rapid": {"model_bytes": 17 * GIB}}}
        worker = check.BasicCheck(self.root, temper)
        document = {"schema": "temper-preset/v1", "preset": {"artifact": "qwen", "engine": "rapid-mlx", "interface": "chat-completions"},
                    "artifacts": {"qwen": {"presentation": {"model_name": "Qwen3.8 27B"}}}}
        catalog = {"engines": {"rapid-mlx": {"adapter": "rapid-mlx/v1"}}}
        source = ("qwen-rapid", {"qwen-rapid.json": canonical_json(document)})
        _, row, _ = worker.trial(self.root / "runtime", source, [], FACTS, {}, catalog)
        self.assertEqual((row["engine"], row["family"]), ("rapid-mlx", "rapid-mlx"))
        document["artifacts"]["qwen"]["presentation"]["model_name"] = "Gemma"
        with self.assertRaisesRegex(Refusal, "declare model_name"):
            worker.trial(self.root / "runtime", ("qwen-rapid", {"qwen-rapid.json": canonical_json(document)}), [], FACTS, {}, catalog)

    def test_study_shared_cache_requires_every_hf_file(self):
        missing_template = {"remaining_downloads": [{"kind": "template", "bytes": 100}]}
        self.assertEqual(check.cache_mode(missing_template, require_complete=True), "private")
        self.assertEqual(check.cache_mode({"remaining_downloads": []}, require_complete=True), "shared")

    def test_wired_growth_stops_even_when_process_footprint_misses_gpu_weights(self):
        from tests.test_watcher import snapshot
        spec = check.watch_spec(self.protocol, FACTS, "llama-server")
        observed = snapshot()
        observed["wired_bytes"] = 28 * GIB
        _, reasons = check.watcher.evaluate_snapshot(spec, observed, baseline_swap_bytes=1024,
                     baseline_wired_bytes=3 * GIB, observed_rss_peaks={}, gap_milliseconds=0)
        self.assertEqual([r["kind"] for r in reasons], ["wired-growth"])
        self.assertEqual(reasons[0]["observed_bytes"], 25 * GIB)


class StudyTest(RepositoryTest):
    def setUp(self):
        super().setUp()
        binary = self.root / "temper"
        binary.write_bytes(b"fake temper")
        self.fake = FakeTemper(cached=False)
        self.temper = Temper(binary, runner=self.fake, progress_runner=self.fake)
        self.evaluator = {"path": "/owned/python", "sha256": "d" * 64, "packages": {"pytest": "test"}}
        self.addCleanup(patch.stopall)
        patch.object(review, "evaluator", return_value=self.evaluator).start()
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.plan = study.plan(self.root, self.temper, presets=[PRESET], files=[], python=Path("/owned/python"),
                               max_runs=4, hours=1, download_gib=20, max_context=131072, request_seconds=600, say=lambda _: None)
        self.subject = study.Study(self.root, self.temper, "qwen-64")

    def start(self):
        self.assertTrue(self.subject.start(self.plan, assume_yes=True))

    def measure(self, *, kind="smoke", purpose="screen", row=None, **kwargs):
        def answer(worker, directory, run, case):
            return {"id": case["id"], "status": "patch-submitted" if kind == "coding" else "passed",
                    "safe_to_cleanup": True, "measurement_valid": True, **(row or {})}
        with patch.object(study.StudyCheck, "measure_case", answer):
            return self.subject.run(PRESET, kind=kind, purpose=purpose, reason="unmeasured hardware question", **kwargs)

    def latest(self):
        state = self.subject.load()
        return self.root / "runs" / state["runs"][-1]["id"]

    def test_plan_is_model_free_and_decline_leaves_no_study(self):
        self.assertFalse(any(args[:2] == ["execution", "prepare"] for args, _ in self.fake.calls))
        self.assertFalse(self.subject.start(self.plan, ask=lambda _: "no"))
        self.assertFalse(self.subject.directory.exists())

    def test_plan_accounts_for_candidates_retained_together(self):
        original = self.fake.plan
        def limited_disk(*args, **kwargs):
            return {**original(*args, **kwargs), "free_disk_bytes": 35 * GIB}
        with patch.object(self.fake, "plan", side_effect=limited_disk):
            with self.assertRaisesRegex(Refusal, "combined retained candidates"):
                study.plan(self.root, self.temper, presets=[PRESET, "qwen3.8-27b-q4xl-splash"], files=[],
                           python=Path("/owned/python"), max_runs=8, hours=1, download_gib=50,
                           max_context=131072, request_seconds=600, say=lambda _: None)

    def test_material_reused_and_owned_cleanup_does_not_touch_user_cache(self):
        self.start()
        self.measure()
        first = json.loads((self.latest() / "run.json").read_text())
        self.measure(kind="coding")
        second = json.loads((self.latest() / "run.json").read_text())
        self.assertEqual(first["cache"], second["cache"])
        self.assertEqual(len(self.subject.load()["charged_candidates"]), 1)
        self.assertFalse(any(args[:2] == ["execution", "remove"] for args, _ in self.fake.calls))
        owned = self.subject.directory / "hf-cache"
        owned.mkdir()
        (owned / "weight").write_text("owned")
        unrelated = self.root / "shared-cache"
        unrelated.mkdir()
        (unrelated / "weight").write_text("user")
        self.subject.close()
        self.subject.close()  # Idempotent, including after material is gone.
        self.assertEqual(self.subject.load()["state"], "closed")
        self.assertFalse(owned.exists())
        self.assertEqual((unrelated / "weight").read_text(), "user")
        self.assertTrue((self.latest() / "result.json").exists())
        self.assertEqual(sum(args[:2] == ["execution", "remove"] for args, _ in self.fake.calls), 1)

    def test_quality_gate_blocks_patch_submission_and_keeps_confirmation_unused(self):
        self.start()
        with self.assertRaisesRegex(Refusal, "smoke pass"):
            self.measure(kind="coding")
        self.measure()
        self.measure(kind="coding")
        self.assertEqual(json.loads((self.latest() / "run.json").read_text())["case_ids"], ["async-stream"])
        with self.assertRaisesRegex(Refusal, "tests and code review"):
            self.measure(kind="cache", purpose="tune")
        atomic_write(self.latest() / "review/tests.json", canonical_json({"evaluator": self.evaluator}))
        with patch.object(review, "verdict", return_value="not-qualified"):
            with self.assertRaisesRegex(Refusal, "reviewed baseline"):
                self.measure(kind="context", purpose="tune")
        with patch.object(review, "verdict", return_value="pass"):
            self.measure(kind="coding", purpose="confirm")
            self.assertEqual(json.loads((self.latest() / "run.json").read_text())["case_ids"], ["template-decorators"])
            atomic_write(self.latest() / "review/tests.json", canonical_json({"evaluator": self.evaluator}))
            with self.assertRaisesRegex(Refusal, "consumed its held-out"):
                self.measure(kind="coding", purpose="tune")

    def test_budget_is_reserved_before_effect_and_survives_new_controller(self):
        self.plan["budgets"]["runs"] = 1
        self.start()
        self.measure()
        reopened = study.Study(self.root, self.temper, "qwen-64")
        with self.assertRaisesRegex(Refusal, "budget exhausted"):
            reopened.run(PRESET, reason="repeat")
        state = reopened.load()
        state["deadline"] = 0
        reopened.save(state)
        with self.assertRaisesRegex(Refusal, "budget exhausted"):
            reopened.run(PRESET, reason="repeat")
        reopened.close()  # Cleanup still works when the work budget has expired.

    def test_uncertain_shutdown_retains_material_and_blocks_new_runs(self):
        self.start()
        self.measure(row={"status": "runtime-failure", "safe_to_cleanup": False})
        with self.assertRaisesRegex(Refusal, "uncertain shutdown"):
            self.subject.close()
        with self.assertRaisesRegex(Refusal, "unfinished or retained"):
            self.measure()
        self.assertEqual(self.subject.load()["state"], "open")

    def test_cleanup_failure_is_recoverable_without_repeating_completed_removals(self):
        self.start()
        self.measure()
        with patch.object(self.temper, "remove", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.subject.close()
        self.assertEqual(self.subject.load()["state"], "closing")
        study.Study(self.root, self.temper, "qwen-64").close()
        self.assertEqual(self.subject.load()["state"], "closed")

    def test_safe_preparation_failure_can_be_closed_without_inference(self):
        self.start()
        with patch.object(self.temper, "prepare", side_effect=OSError("download failed")):
            with self.assertRaises(OSError):
                self.measure()
        self.subject.close()
        self.assertEqual(json.loads((self.latest() / "run.json").read_text())["state"], "cancelled")

    def test_settings_change_is_allowed_but_new_weights_engine_template_are_not(self):
        base = json.loads(self.plan["candidates"][0]["lock"])
        variant = copy.deepcopy(base)
        preset = variant["records"]["presets"][PRESET]
        preset["profiles"][0]["context_window_tokens"] = 131072
        preset["profiles"][0]["engine_config"] = {"batch_size": 1024, "cache_type_k": "q8_0"}
        study.validate_variant(base, variant, 131072)
        with self.assertRaisesRegex(Refusal, "context ceiling"):
            study.validate_variant(base, variant, 65536)
        for field, value in (("engine", "rapid-mlx"), ("artifact", "qwen-q5"), ("template", "different")):
            changed = copy.deepcopy(variant)
            changed["records"]["presets"][PRESET][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(Refusal, "fixed material"):
                study.validate_variant(base, changed, 131072)

    def test_owned_runtime_symlink_is_never_passed_to_temper_remove(self):
        self.start()
        self.measure()
        runtime = self.subject.directory / "runtime"
        runtime.mkdir(exist_ok=True)
        target = self.root / "user-installation"
        target.mkdir()
        (runtime / PRESET).symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(Refusal, "symlink"):
            self.subject.close()
        self.assertFalse(any(args[:2] == ["execution", "remove"] for args, _ in self.fake.calls))


class ReviewTest(RepositoryTest):
    def test_all_skipped_tests_are_not_behavior_success(self):
        from fieldkit_runtime.coding.evaluation import group_passes
        group = {"exit_code": 0, "summary": {"tests": 14, "skipped": 14}}
        self.assertFalse(group_passes(group))
        group["summary"]["skipped"] = 2
        self.assertTrue(group_passes(group))

    def test_failed_tests_cannot_be_overridden_by_a_passing_review_and_stale_reviews_refuse(self):
        directory = self.root / "run"
        directory.mkdir()
        result = {"state": "complete", "protocol": {"id": "basic-check", "revision": 2}, "workload": {"kind": "coding"},
                  "cases": [{"id": "async-stream", "status": "patch-submitted", "patch": {"diff_sha256": "a" * 64}}]}
        atomic_write(directory / "result.json", canonical_json(result))
        tests = {"result_sha256": file_digest(directory / "result.json"), "cases": [{"id": "async-stream", "status": "tested", "all_pass": False}]}
        atomic_write(directory / "review/tests.json", canonical_json(tests))
        assessment = {"result_sha256": tests["result_sha256"], "reviewer": "reviewing agent", "cases": [
            {"id": "async-stream", "patch_sha256": "a" * 64, "verdict": "pass", "behavior": "Context lifetime inspected.",
             "regressions": "API inspected.", "test_quality": "Wrong route in candidate test."}]}
        path = self.root / "assessment.json"
        atomic_write(path, canonical_json(assessment))
        with self.assertRaisesRegex(Refusal, "passing original"):
            review.assess(self.root, directory, path)
        assessment["cases"][0]["verdict"] = "fail"
        atomic_write(path, canonical_json(assessment))
        review.assess(self.root, directory, path)
        self.assertEqual(review.verdict(directory), "not-qualified")
        atomic_write(directory / "result.json", canonical_json({**result, "changed": True}))
        with self.assertRaisesRegex(Refusal, "no longer matches"):
            review.verdict(directory)
