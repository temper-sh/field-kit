"""A contributor completes one configuration without funding the whole matrix."""
import argparse
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fieldkit_runtime.catalog import QuestionCatalog, Refusal, canonical_json, digest, load_question_material, parse_machine_facts
from fieldkit_runtime.experiments.qwen.chunk_contributor import contribute
from fieldkit_runtime.experiments.qwen.chunks import ChunkStudy, SELECTOR, select_package, selected_cells
from fieldkit_runtime.experiments.qwen.splash_study import SplashStudy
from fieldkit_runtime.experiments.qwen.witness import inspect as review, inspect_bytes as review_bytes
from fieldkit_runtime.workflow import CommandFailure, CommandResult, load_session
from tests.test_catalog import ROOT
from tests.test_splash_study import MatrixRunner, SyntheticStudy, machine


class SyntheticChunkStudy(SyntheticStudy, ChunkStudy):
    """Keep the real selection, task and session flow; replace native effects."""


class ChunkRunner(MatrixRunner):
    def __init__(self, **settings):
        super().__init__(**settings)
        self.cache_roots = []
        self.prepared = []
        self.fail_prepare = False

    def execution(self, lock):
        preset = json.loads(lock.read_bytes())["preset"]
        return {"schema": "temper-execution/v1", "profile": preset, "layouts": [preset],
                "lock_sha256": digest(lock.read_bytes()), "execution_digest": "a"*64,
                "request_defaults": {preset: {}}}

    def __call__(self, arguments, timeout, *, environment):
        argv = list(arguments)
        if argv[1:3] == ["execution", "prepare"]:
            root = Path(argv[argv.index("--root") + 1])
            cache = Path(environment["HF_HUB_CACHE"])
            if cache != root / "hf-cache":
                raise AssertionError("downloads escaped the selected run")
            cache.mkdir(exist_ok=True)
            preset = self.execution(Path(argv[argv.index("--lock") + 1]))["profile"]
            (cache / preset).write_text("synthetic downloaded weights")
            self.cache_roots.append(cache)
            self.prepared.append(preset)
            if self.fail_prepare:
                return CommandResult(b"", b"injected download failure", 1)
        if "--action" in argv:
            args = argparse.Namespace(**{argv[i][2:].replace("-", "_"): argv[i+1] for i in range(2, len(argv), 2)})
            study = SyntheticChunkStudy(args, Path(argv[1]).parent)
            study.requests = self.requests
            for key, value in self.settings.items():
                setattr(study, key, value)
            self.actions.append(study.action["id"])
            with patch("fieldkit_runtime.experiments.qwen.splash_study.monitored_call", return_value={}):
                result = study.run()
            Path(args.report).write_bytes(canonical_json(result))
            return CommandResult(b"", b"", 0)
        return super().__call__(argv, timeout)


class ChunkTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="qwen chunks ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        shutil.copytree(ROOT / "catalog", self.root / "catalog")
        (self.root / ".local").mkdir()
        (self.root / ".local/temper").write_bytes(b"fixture temper")
        self.shared = self.root / "shared-hub"
        self.shared.mkdir()
        (self.shared / "existing-model").write_bytes(b"pre-existing user data")
        self.raw = machine(48)
        self.runner = ChunkRunner()
        self.prompts = []
        self.output = io.StringIO()

    def run_chunk(self, *, free=100 * 1024**3, answer="yes", **overrides):
        arguments = dict(temper=None, preview=False, new=False, configuration=None, next=False, pause_after_task=False)
        arguments.update(overrides)
        def consent(prompt):
            self.prompts.append(prompt)
            return answer
        with redirect_stdout(self.output), patch.dict("os.environ", {"HF_HUB_CACHE": str(self.shared)}), \
                patch("fieldkit_runtime.experiments.qwen.chunk_contributor.shutil.disk_usage", return_value=SimpleNamespace(free=free)):
            return contribute(argparse.Namespace(**arguments), self.root, input_fn=consent,
                facts_reader=lambda _: (parse_machine_facts(self.raw), self.raw), runner=self.runner)

    def sessions(self):
        return sorted((self.root / ".local").glob("qwen-5-*.session.json"))

    def test_default_run_measures_only_q4_exports_and_reclaims_its_downloads(self):
        self.run_chunk()

        self.assertEqual([task for _, task in self.runner.requests], ["async-stream", "template-decorators"])
        self.assertEqual(self.runner.prepared, ["splash-q4"])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(len(self.sessions()), 1)
        session = load_session(self.sessions()[0])
        self.assertEqual(session["cleanup"], "complete")
        self.assertFalse(Path(session["paths"]["root"]).exists())
        self.assertTrue(all(not cache.exists() for cache in self.runner.cache_roots))
        self.assertEqual((self.shared / "existing-model").read_bytes(), b"pre-existing user data")
        export = next((self.root / "runs").glob("*/result.json"))
        result = review(export, self.root / "catalog/packages/qwen-machine-study@5/package.json")
        self.assertEqual(len(result["answers"]["completed-work"]["value"]["rows"]), 2)
        plan = json.loads(Path(session["plan"]["path"]).read_bytes())
        self.assertEqual(len(plan["investigation"]["actions"]), 3)
        self.assertLess(plan["cost_ceiling"]["temporary_disk_bytes_max"], 100 * 1024**3)
        self.assertNotIn("124.2", self.output.getvalue())

    def test_task_checkpoint_resumes_without_repeating_the_first_attempt(self):
        self.run_chunk(pause_after_task=True)
        path = self.sessions()[0]
        before = load_session(path)
        first = copy.deepcopy(before["attempts"][-1])
        self.assertEqual(before["state"], "awaiting-action")
        self.assertEqual(len(self.runner.requests), 1)
        self.assertTrue(Path(before["paths"]["root"]).exists())
        self.assertTrue(next((self.root / "runs").glob("*/report.md")).is_file())
        self.assertFalse(list((self.root / "runs").glob("*/result.json")))

        self.run_chunk()

        after = load_session(path)
        self.assertEqual(after["attempts"][1], first)
        self.assertEqual(len(self.runner.requests), 2)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(after["cleanup"], "complete")

    def test_completed_configuration_requires_an_explicit_next_selection(self):
        self.run_chunk()
        self.run_chunk()
        self.assertEqual(len(self.runner.requests), 2)
        self.assertEqual(len(self.prompts), 1)

        self.run_chunk(next=True)

        self.assertEqual(self.runner.prepared, ["splash-q4", "splash-q5"])
        self.assertEqual(len(self.runner.requests), 4)
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(len(self.sessions()), 2)
        self.run_chunk(next=True)
        self.assertEqual(self.runner.prepared[-1], "llama-q5")

    def test_explicit_other_configuration_never_prepares_q4(self):
        self.run_chunk(configuration="llama-q6")

        self.assertEqual(self.runner.prepared, ["llama-q6"])
        self.assertEqual(len(self.runner.requests), 2)
        session = load_session(self.sessions()[0])
        installations = {row["installation"] for row in session["answers"]["completed-work"]["value"]["rows"]}
        initial = session["attempts"][0]["arguments"]
        self.assertEqual(installations, {initial[initial.index("--installation") + 1]})
        for export in (self.root / "runs").glob("*/result.json"):
            review(export, self.root / "catalog/packages/qwen-machine-study@5/package.json")

    def test_task_preparation_reuses_the_selected_initial_installation(self):
        source = QuestionCatalog.load(ROOT / "catalog/questions.json").find(SELECTOR)
        study = SplashStudy.__new__(SplashStudy)
        study.args = SimpleNamespace(temper=self.root / ".local/temper", root=self.root,
                                    model="llama-q6", installation="selected-installation")
        study.package_root = source.package_root
        study.protocol = json.loads(source.files["protocol.json"])
        study.limit = 36 * 1024**3
        execution = {"profile": "llama-q6", "layouts": ["llama-q6"]}
        preparations = []
        def command(argv, timeout):
            self.assertEqual(argv[argv.index("--installation") + 1], "selected-installation")
            if argv[1] == "prepare":
                preparations.append(argv)
                return canonical_json({"schema": "temper-execution-material/v1", "execution": execution,
                                       "generation": "b"*64, "binding": "schema: temper-field-kit-binding/v1\n"})
            return canonical_json({"schema": "temper-execution-paths/v1", "execution": execution})
        study.command = command
        with patch("fieldkit_runtime.experiments.qwen.splash_study.configure_execution", return_value={"context_execution_sha256": "c"*64}), \
                patch("fieldkit_runtime.experiments.qwen.splash_study.inspect_execution", return_value=execution):
            for cell in selected_cells(study.protocol, "llama-q6"):
                material = study.configure(cell, self.root / cell["id"])
                self.assertEqual(material["installation"], "selected-installation")
        self.assertEqual(len(preparations), 2)

    def test_witness_rejects_a_different_selection_or_ineligible_machine(self):
        self.run_chunk(configuration="llama-q6")
        export = next((self.root / "runs").glob("*/result.json"))
        original = json.loads(export.read_bytes())
        package = self.root / "catalog/packages/qwen-machine-study@5/package.json"
        changed = copy.deepcopy(original)
        changed["session"]["package"]["selector"] = "qwen-chunk-splash-q4@5"
        with self.assertRaisesRegex(Refusal, "bind the supplied package"):
            review_bytes(canonical_json(changed), package)
        changed = copy.deepcopy(original)
        changed["session"]["machine_facts"]["physical_memory_bytes"] = 36 * 1024**3
        changed["plan"]["machine"]["facts"] = changed["session"]["machine_facts"]
        changed["session"]["plan"]["sha256"] = digest(canonical_json(changed["plan"]))
        with self.assertRaisesRegex(Refusal, "not applicable"):
            review_bytes(canonical_json(changed), package)

    def test_interrupting_the_second_task_preserves_the_first_and_never_replays(self):
        self.run_chunk(pause_after_task=True)
        path = self.sessions()[0]
        first = copy.deepcopy(load_session(path)["attempts"][-1])
        with patch.object(SyntheticChunkStudy, "coding_case", side_effect=KeyboardInterrupt("injected interruption")):
            self.run_chunk()
        session = load_session(path)
        self.assertEqual(session["attempts"][1], first)
        rows = session["answers"]["completed-work"]["value"]["rows"]
        self.assertEqual(rows[-1]["failure"]["kind"], "interrupted")
        self.assertEqual(session["cleanup"], "complete")
        self.run_chunk()
        self.assertEqual(len(self.runner.requests), 1)
        export = next((self.root / "runs").glob("*/result.json"))
        review(export, self.root / "catalog/packages/qwen-machine-study@5/package.json")

    def test_pending_task_prevents_another_configuration_or_new_allocation(self):
        self.run_chunk(pause_after_task=True)
        for selection in ({"next": True}, {"configuration": "llama-q6"}, {"new": True}):
            with self.subTest(selection=selection), self.assertRaisesRegex(Refusal, "unfinished"):
                self.run_chunk(**selection)
        self.assertEqual(len(self.runner.requests), 1)
        self.assertEqual(len(self.sessions()), 1)

    def test_uncertain_shutdown_retains_owned_downloads_and_refuses_later_tasks(self):
        self.runner = ChunkRunner(unsafe=True)
        with self.assertRaisesRegex(Refusal, "shutdown"):
            self.run_chunk()
        session = load_session(self.sessions()[0])
        self.assertTrue(Path(session["paths"]["root"]).is_dir())
        self.assertEqual(len(self.runner.requests), 1)
        with self.assertRaisesRegex(Refusal, "shutdown"):
            self.run_chunk()
        self.assertEqual(len(self.runner.requests), 1)

    def test_failed_preparation_retains_failure_and_can_retry_without_measurement_replay(self):
        self.runner.fail_prepare = True
        with self.assertRaises(CommandFailure):
            self.run_chunk()
        self.assertEqual(self.runner.requests, [])
        self.runner.fail_prepare = False

        self.run_chunk()

        session = load_session(self.sessions()[0])
        self.assertEqual([row["state"] for row in session["attempts"][:2]], ["failed", "complete"])
        self.assertEqual(len(self.runner.requests), 2)

    def test_preview_decline_and_insufficient_disk_create_no_session_or_download(self):
        before = sorted(self.root.rglob("*"))
        self.run_chunk(preview=True)
        self.assertEqual(sorted(self.root.rglob("*")), before)
        self.run_chunk(answer="no")
        with self.assertRaisesRegex(Refusal, "configuration requires"):
            self.run_chunk(free=10 * 1024**3)
        self.assertEqual(self.sessions(), [])
        self.assertEqual(self.runner.cache_roots, [])
        self.assertEqual(self.runner.requests, [])

    def test_q8_admission_happens_before_any_download(self):
        self.raw = machine(48).replace(b"wired_limit_mib: 36864", b"wired_limit_mib: 27648")
        with self.assertRaisesRegex(Refusal, "Q8 weights"):
            self.run_chunk(configuration="llama-q8")
        self.assertEqual(self.runner.cache_roots, [])
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.sessions(), [])

    def test_context_ladder_is_separate_and_stops_at_its_first_unsuccessful_point(self):
        self.raw = machine(36)
        self.runner = ChunkRunner(context_fails=True)
        self.run_chunk(configuration="splash-q4-context")
        self.assertEqual(self.runner.actions, ["context-32768", "finish-study"])
        self.assertEqual(self.runner.prepared, ["splash-q4"])
        export = next((self.root / "runs").glob("*/result.json"))
        review(export, self.root / "catalog/packages/qwen-machine-study@5/package.json")

    def test_each_selection_is_a_valid_frozen_package_and_names_its_source(self):
        source = QuestionCatalog.load(ROOT / "catalog/questions.json").find(SELECTOR)
        protocol = json.loads(source.files["protocol.json"])
        for choice in protocol["configurations"]:
            with self.subTest(configuration=choice["id"]):
                selected = select_package(source, choice["id"])
                root = self.root / choice["id"]
                root.mkdir()
                (root / "package.json").write_bytes(selected.package_data)
                for name, data in selected.files.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                checked = load_question_material(root / "package.json")
                self.assertEqual(checked.package_sha256, selected.package_sha256)
                self.assertEqual(checked.package["origin"]["source_sha256"], source.package_sha256)
