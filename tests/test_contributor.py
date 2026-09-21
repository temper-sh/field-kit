"""A first-time contributor gets an attributable result without maintainer steps."""
from __future__ import annotations

import argparse
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fieldkit_runtime.catalog import Refusal, canonical_json, digest, parse_machine_facts
from fieldkit_runtime.experiments.qwen.contributor import contribute
from fieldkit_runtime.experiments.qwen.method import variant_records
from fieldkit_runtime.experiments.qwen.protocol import Study
from fieldkit_runtime.experiments.qwen.witness import inspect as review
from fieldkit_runtime.workflow import CommandResult, load_session
from tests.test_catalog import ROOT, facts_bytes
from tests.test_workflow import FakeRunner


class QuietProbe:
    def __init__(self, safe=True): self.safe = safe
    def start(self): pass
    def finish(self):
        return {"samples": 2, "roles": {"engine": {"rss_bytes_max": 20 * 1024**3}},
                "swap_growth_bytes": 0, "issues": [], "safe_to_cleanup": self.safe}


class SyntheticStudy(Study):
    """Keep real protocol/workflow orchestration; replace only native effects."""
    safe = True
    def configure(self, directory, variant, *, cache=None, window=None):
        self.active_configuration = variant
        records = variant_records(self.lock, self.model, variant, cache=cache, window=window)
        return {"generation": "b" * 64, "execution_lock_sha256": "c" * 64,
                "execution_digest": "d" * 64, "settings": records["layouts"][self.model], "binding_sha256": "e" * 64}
    def probe(self, directory, material): return QuietProbe(self.safe)


class StudyRunner(FakeRunner):
    def __init__(self, *, interrupt_at=None, unsafe=False, gain=False):
        super().__init__()
        self.interrupt_at = interrupt_at
        self.unsafe = unsafe
        self.gain = gain
        self.requests = 0
        self.messages = []
        self.actions = []

    def __call__(self, arguments, timeout):
        argv = list(arguments)
        if argv[-1:] == ["version"]:
            return CommandResult(b"temper 0.1.0-alpha.7\n", b"", 0)
        if "--action" in argv:
            pairs = {argv[index][2:].replace("-", "_"): argv[index + 1] for index in range(2, len(argv), 2)}
            args = argparse.Namespace(**pairs)
            study = SyntheticStudy(args, Path(argv[1]).parent)
            study.safe = not self.unsafe
            self.actions.append(study.action["id"])
            def response(probe, model, messages, expected, window, reserve, request_timeout):
                self.requests += 1
                self.messages.append(copy.deepcopy(messages))
                if self.requests == self.interrupt_at:
                    raise KeyboardInterrupt("synthetic contributor stop")
                tokens = messages[0]["content"].count(" x") + 200 if messages[0]["content"].startswith("Read this ledger.") else 1200
                seconds = 8.0 if self.gain and study.active_configuration == "batch-1024" else 10.0
                return {"content": json.dumps(expected), "correct": True, "finish_reason": "stop", "measurement_valid": True,
                        "service_seconds": seconds, "first_token_seconds": 1.0, "first_answer_seconds": 2.0,
                        "usage": {"prompt_tokens": tokens, "completion_tokens": 100},
                        "prefill_tokens_per_second": 120.0, "generation_tokens_per_second": 10.0,
                        "timings": {"prompt_n": tokens, "prompt_ms": tokens * 1000 / 120, "predicted_n": 100, "predicted_ms": 10000, "cache_n": 0}}
            def native(probe, path, payload, timeout):
                if path == "/apply-template":
                    return {"prompt": "\n".join(message["content"] for message in payload["messages"])}
                return {"tokens": [0] * (payload["content"].count(" x") + 200)}
            with patch("fieldkit_runtime.experiments.qwen.protocol.measure_chat", side_effect=response), patch("fieldkit_runtime.experiments.qwen.protocol.monitored_call", side_effect=native):
                report = study.run()
            Path(args.report).write_bytes(canonical_json(report))
            return CommandResult(b"", b"", 0)
        return super().__call__(argv, timeout)


class ContributorTest(unittest.TestCase):
    def setUp(self):
        disk = patch("fieldkit_runtime.experiments.qwen.contributor.shutil.disk_usage", return_value=SimpleNamespace(free=100 * 1024**3))
        disk.start()
        self.addCleanup(disk.stop)

    def setup_clone(self):
        temporary = tempfile.TemporaryDirectory(prefix="field kit ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        shutil.copytree(ROOT / "catalog", root / "catalog")
        (root / ".local").mkdir()
        (root / ".local/temper").write_bytes(b"synthetic signed release\n")
        args = argparse.Namespace(temper=None, tuning="none", preview=False, new=False)
        raw = facts_bytes()
        return root, args, lambda _: (parse_machine_facts(raw), raw)

    def test_contributor_approves_once_gets_reviewable_result_and_reopening_does_not_repeat_work(self):
        root, args, facts = self.setup_clone()
        runner = StudyRunner()
        prompts = []
        def approve(prompt):
            prompts.append(prompt)
            return "yes"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(contribute(args, root, input_fn=approve, facts_reader=facts, runner=runner), 0)
        self.assertEqual(len(prompts), 1)
        export = next((root / "runs").glob("*/result.json"))
        result = review(export, root / "catalog/packages/qwen-machine-study@2/package.json")
        self.assertEqual(result["cleanup"], "complete")
        self.assertEqual(result["answers"]["interaction"]["value"]["correct_cases"], 8)
        self.assertEqual(runner.actions, ["measure-baseline", "finish-study"])
        consent = json.loads((root / ".local/qwen-study-2-session.json").read_bytes())["consent"]
        session = load_session(Path(consent["session"]))
        self.assertFalse(Path(session["paths"]["root"]).exists())
        with redirect_stdout(io.StringIO()):
            contribute(args, root, input_fn=lambda _: self.fail("completed run asked for consent again"), facts_reader=facts, runner=runner)
        self.assertEqual(runner.requests, 8)
        # Continue, rewind and return use the actual delivered conversation.
        cases = result["answers"]["completed-work"]["value"]["cases"]
        self.assertEqual(runner.messages[1][1]["content"], cases[0]["content"])
        self.assertEqual(runner.messages[4][:2], runner.messages[3][:2])
        self.assertEqual(len(runner.messages[4]), 3)
        self.assertEqual(len(runner.messages[6]), 5)
        package = root / "catalog/packages/qwen-machine-study@2/package.json"
        checked = subprocess.run([sys.executable, "-B", "-S", "-m", "fieldkit_runtime", "witness",
                                  "--input", str(export), "--package", str(package)],
                                 cwd=ROOT, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["cleanup"], "complete")
        packet = json.loads(export.read_bytes())
        self.assertIn("generated model answers", packet["report"])
        self.assertIn("local paths", packet["report"])
        packet["report"] += "changed"
        export.write_bytes(canonical_json(packet))
        with self.assertRaisesRegex(Refusal, "altered"):
            review(export, package)

    def test_declining_creates_no_session_or_download(self):
        root, args, facts = self.setup_clone()
        runner = StudyRunner()
        with redirect_stdout(io.StringIO()):
            contribute(args, root, input_fn=lambda _: "no", facts_reader=facts, runner=runner)
        self.assertFalse(list((root / ".local").glob("*.session.json")))
        self.assertFalse((root / "runs").exists())
        self.assertFalse(any(call[1:3] == ["execution", "prepare"] for call in runner.calls))

    def test_dispatched_session_pointer_requires_its_producing_runtime(self):
        root, args, facts = self.setup_clone()
        pointer = root / ".local/contributor-session.json"
        pointer.write_bytes(b"retained revision 1 pointer")
        runner = StudyRunner()
        with self.assertRaisesRegex(Refusal, "revision 1 study"):
            contribute(args, root, input_fn=lambda _: self.fail("old study prompted for a new run"), facts_reader=facts, runner=runner)
        self.assertEqual(pointer.read_bytes(), b"retained revision 1 pointer")
        self.assertFalse(runner.calls)
        self.assertFalse((root / "runs").exists())
        self.assertFalse(any(call[1:3] == ["execution", "prepare"] for call in runner.calls))

    def test_preview_is_read_only_and_does_not_request_consent(self):
        root, args, facts = self.setup_clone()
        args.preview = True
        before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        with redirect_stdout(io.StringIO()):
            contribute(args, root, input_fn=lambda _: self.fail("preview asked for consent"), facts_reader=facts, runner=StudyRunner())
        self.assertEqual(before, sorted(str(p.relative_to(root)) for p in root.rglob("*")))

    def test_insufficient_disk_refuses_before_consent_or_model_download(self):
        root, args, facts = self.setup_clone()
        runner = StudyRunner()
        with patch("fieldkit_runtime.experiments.qwen.contributor.shutil.disk_usage", return_value=SimpleNamespace(free=1024**3)), self.assertRaisesRegex(Refusal, "free"):
            contribute(args, root, input_fn=lambda _: self.fail("insufficient disk requested consent"), facts_reader=facts, runner=runner)
        self.assertFalse(list((root / ".local").glob("*.session.json")))
        self.assertFalse(any(call[1:3] == ["execution", "prepare"] for call in runner.calls))

    def test_graceful_interruption_returns_partial_unknown_evidence_without_retry(self):
        root, args, facts = self.setup_clone()
        args.tuning = "both"
        runner = StudyRunner(interrupt_at=3)
        with redirect_stdout(io.StringIO()):
            contribute(args, root, input_fn=lambda _: "yes", facts_reader=facts, runner=runner)
        result = review(next((root / "runs").glob("*/result.json")), root / "catalog/packages/qwen-machine-study@2/package.json")
        self.assertEqual(result["answers"]["completed-work"]["state"], "unknown")
        self.assertEqual(len(result["answers"]["completed-work"]["value"]["cases"]), 2)
        limits = result["answers"]["limits"]["value"]
        self.assertEqual(limits["incomplete_baseline_cases"], ["registry"])
        self.assertNotIn("registry", limits["unattempted_baseline_cases"])
        self.assertEqual(result["cleanup"], "complete")
        self.assertEqual(runner.actions, ["measure-baseline", "finish-study"])

    def test_uncertain_shutdown_retains_installation_and_refuses_completion(self):
        root, args, facts = self.setup_clone()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(Refusal, "shutdown"):
            contribute(args, root, input_fn=lambda _: "yes", facts_reader=facts, runner=StudyRunner(unsafe=True))
        session_path = next((root / ".local").glob("*.session.json"))
        session = load_session(session_path)
        self.assertTrue(Path(session["paths"]["root"]).is_dir())
        self.assertFalse((root / "runs").exists())

    def test_flag_allocation_runs_screens_and_retains_baseline_when_no_gain(self):
        root, args, facts = self.setup_clone()
        args.tuning = "flags"
        runner = StudyRunner()
        with redirect_stdout(io.StringIO()):
            contribute(args, root, input_fn=lambda _: "yes", facts_reader=facts, runner=runner)
        result = review(next((root / "runs").glob("*/result.json")), root / "catalog/packages/qwen-machine-study@2/package.json")
        tuning = result["answers"]["tuning"]["value"]
        self.assertEqual(tuning["decision"]["selection"], "baseline")
        self.assertEqual([run["configuration"] for run in tuning["screens"]], ["batch-1024", "mtp-off", "cache-reference", "kv-q4"])
        self.assertEqual(len(tuning["confirmation"]), 0)
        self.assertEqual(result["answers"]["interaction"]["value"]["total_service_seconds"], 80)

    def test_full_study_confirms_a_candidate_and_returns_separate_context_evidence(self):
        root, args, facts = self.setup_clone()
        args.tuning = "both"
        runner = StudyRunner(gain=True)
        with redirect_stdout(io.StringIO()):
            contribute(args, root, input_fn=lambda _: "yes", facts_reader=facts, runner=runner)
        result = review(next((root / "runs").glob("*/result.json")), root / "catalog/packages/qwen-machine-study@2/package.json")
        tuning = result["answers"]["tuning"]["value"]
        self.assertEqual(tuning["decision"]["selection"], "batch-1024")
        self.assertEqual([run["configuration"] for run in tuning["confirmation"]], ["baseline", "batch-1024", "batch-1024", "baseline"])
        self.assertEqual(len(tuning["context_points"]), 10)
        self.assertEqual(tuning["context_summary"]["q8"]["highest_witnessed_input_tokens"], 125952)
        self.assertEqual(tuning["context_points"][0]["material"]["settings"]["engine_config"]["batch_tokens"], 512)
        self.assertEqual(result["answers"]["interaction"]["value"]["total_service_seconds"], 80)
