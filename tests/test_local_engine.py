"""Local engines: evaluated before Temper supports them, never catalog evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fieldkit_runtime import attempt, recovery
from fieldkit_runtime.approval import preview, summary
from fieldkit_runtime.common import Refusal, canonical_json, load_json
from fieldkit_runtime.local_probe import LOCAL_STATUS_SCHEMA
from fieldkit_runtime.records import AttemptFiles
from fieldkit_runtime.study import Study
from fieldkit_runtime.subjects import local_candidate, software_digest
from fieldkit_runtime.export import export_study
from tests.test_research import FakeProbe, FakeTemper, fake_context

class LocalEngineTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        FakeTemper.reset()
        build = self.root / "llama.cpp/build/bin"
        build.mkdir(parents=True)
        self.server = build / "llama-server"
        self.server.write_text("#!/bin/sh\n")
        self.server.chmod(0o755)
        (build / "libggml-metal.dylib").write_bytes(b"metal")
        self.weights = self.root / "flint.gguf"
        self.weights.write_bytes(b"w" * 4096)
        self.brief = self.root / "brief.md"
        self.brief.write_text("Is the Flint build worth a Temper integration?")
        self.workload = self.root / "workload.json"
        self.workload.write_bytes(canonical_json({"question": "Smoke", "request": {
            "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 8}, "expect": {"text": "OK"}}))
        self.spec = self.declare()
        self.probes = []

    def declare(self, name="flint.json", **changes):
        spec = {"schema": "field-kit-local-engine/v1", "id": "flint-q4-pr", "interface": "chat-completions",
                "engine_adapter": "llama-server/v1", "executable": str(self.server),
                "arguments": ["--host", "127.0.0.1", "--port", "{port}", "-m", "{file:weights}", "--n-cpu-moe", "4"],
                "files": {"weights": str(self.weights)}, "model": "flint",
                "source": "llama.cpp pull request 12345 at 0123abc, built locally", "context_window_tokens": 32768}
        spec.update(changes)
        path = self.root / name
        path.write_text(json.dumps(spec))
        return path

    def open_study(self):
        context = fake_context(self.call)
        context = type(context)(**{**context.__dict__, "local_probe": self.recording_probe})
        study = Study(self.root, "flint", context)
        self.document = preview(FakeTemper(), [], self.brief, hours=1, download_gib=0, engines_local=[self.spec],
                                weights_cache=self.root / "unused-cache", context=context, root=study.runtime())
        study.initialize(self.document, FakeTemper())
        return study

    def recording_probe(self, **options):
        self.probes.append(options)
        return FakeProbe(**options)

    def call(self, probe, path, payload, timeout, **kwargs):
        self.calls.append((path, payload.get("model")))
        if kwargs.get("method") == "GET":
            return {"status": "ok"}
        kwargs["stream_reader"].raw_path.write_text("raw")
        return {"message": {"content": "OK", "reasoning": "", "tool_calls": []}, "finish_reason": "stop",
                "stream_complete": True, "timings": {"prompt_n": 10, "prompt_ms": 5, "predicted_n": 1, "predicted_ms": 5}}

    def test_declaration_records_exact_identity_and_refuses_unclear_commands(self):
        row = local_candidate(self.spec)
        self.assertEqual(row["subject"], "local")
        self.assertEqual(row["files"]["weights"]["bytes"], 4096)
        self.assertEqual(row["files"]["weights"]["sha256_source"], "computed")
        before = row["executable"]["software_sha256"]
        (self.server.parent / "libggml-metal.dylib").write_bytes(b"rebuilt")
        self.assertNotEqual(software_digest(self.server), before)
        blob = self.root / "hub/models--o--m/blobs" / ("a" * 64)
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"x")
        named = local_candidate(self.declare("blob.json", files={"weights": str(blob)}))
        self.assertEqual(named["files"]["weights"]["sha256"], "a" * 64)
        for changes, message in (({"arguments": ["-m", "{file:weights}"]}, "port"),
                                 ({"arguments": ["{port}", "{file:draft}"]}, "unknown placeholders"),
                                 ({"executable": "llama-server"}, "absolute"),
                                 ({"interface": "reranking"}, "does not evaluate")):
            with self.subTest(message=message), self.assertRaisesRegex(Refusal, message):
                local_candidate(self.declare("bad.json", **changes))

    def test_attempt_runs_without_temper_and_is_research_evidence(self):
        self.calls = []
        study = self.open_study()
        directory = study.reserve("flint-q4-pr", self.workload, "Smoke the pull-request build")
        attempt.execute(study, directory)
        result = load_json(directory / "result.json", 64 * 1024**2)
        self.assertEqual((result["state"], result["subject"]), ("complete", "local"))
        self.assertIn("research", result["evidence"])
        self.assertNotIn("installation", result)
        self.assertEqual([call[0] for call in FakeTemper.calls if call[0] in ("prepare", "remove")], [])
        self.assertEqual(self.calls, [("/health", None), ("/v1/chat/completions", "flint")])
        argv = self.probes[0]["argv"]
        self.assertEqual(argv[0], str(self.server.resolve()))
        self.assertIn(str(self.weights.resolve()), argv)
        self.assertEqual(argv[argv.index("--port") + 1], self.probes[0]["listen"].rsplit(":", 1)[1])
        self.assertIn("weights", result["storage"]["files"])
        self.assertTrue(result["sessions"][0]["steps"][0]["correct"])
        summary_row = summary(self.document)["candidates"][0]
        self.assertIn("never a catalog profile", summary_row["evidence"])
        self.assertEqual(study.close()["state"], "closed")
        self.assertEqual([call for call in FakeTemper.calls if call[0] == "remove"], [])
        self.assertTrue(self.weights.exists())

    def test_a_harness_reaches_the_local_engine_routes_directly(self):
        self.calls = []
        script = self.root / "harness.py"
        script.write_text("import os, pathlib\np=pathlib.Path(os.environ['FIELD_KIT_OUTPUT_DIR'])\n"
                          "(p/'native.txt').write_text(os.environ['FIELD_KIT_NATIVE_URL'])\n"
                          "(p/'base.txt').write_text(os.environ['FIELD_KIT_BASE_URL'])\n")
        harness = self.root / "harness.json"
        harness.write_bytes(canonical_json({"question": "Coding task", "command": ["{python}", str(script)], "cwd": "."}))
        study = self.open_study()
        directory = study.reserve("flint-q4-pr", harness, "Run the coding harness against the local build")
        attempt.execute(study, directory)
        self.assertEqual(load_json(directory / "result.json", 64 * 1024**2)["state"], "complete")
        output = directory / "sessions/task/output"
        self.assertEqual((output / "native.txt").read_text(), "http://" + self.probes[0]["listen"])
        self.assertEqual((output / "base.txt").read_text(), "http://" + self.probes[0]["listen"] + "/v1")

    def test_changed_files_are_refused_before_the_engine_starts(self):
        self.calls = []
        study = self.open_study()
        directory = study.reserve("flint-q4-pr", self.workload, "Smoke")
        self.weights.write_bytes(b"other" * 1000)
        attempt.execute(study, directory)
        result = load_json(directory / "result.json", 64 * 1024**2)
        self.assertEqual(result["state"], "failed")
        self.assertIn("changed since approval", result["error"]["message"])
        self.assertEqual(self.probes, [])

    def test_arguments_may_change_but_binary_files_and_kind_may_not(self):
        self.calls = []
        study = self.open_study()
        variant = self.declare("variant.json", arguments=["--port", "{port}", "-m", "{file:weights}", "--n-cpu-moe", "8"])
        directory = study.reserve("flint-q4-pr", self.workload, "Try more MoE layers on CPU", engine_path=variant)
        self.assertIn("8", json.loads((directory / "local-engine.json").read_text())["arguments"])
        row = load_json(directory / "run.json")
        row.update(state="complete", safe_to_cleanup=True)
        (directory / "run.json").write_text(json.dumps(row))
        (self.root / "other-build").mkdir()
        other = self.root / "other-build/llama-server"
        other.write_text("#!/bin/sh\n# another build\n")
        other.chmod(0o755)
        for path, message in ((self.declare("rebuilt.json", executable=str(other)), "software"),
                              (self.declare("weights.json", files={"weights": str(self.brief)}), "files")):
            with self.subTest(message=message), self.assertRaisesRegex(Refusal, message):
                study.reserve("flint-q4-pr", self.workload, "Changed material", engine_path=path)
        with self.assertRaisesRegex(Refusal, "same subject kind"):
            study.reserve("flint-q4-pr", self.workload, "Mixed", lock_path=self.spec)

    def test_export_verifies_the_frozen_declaration(self):
        self.calls = []
        study = self.open_study()
        directory = study.reserve("flint-q4-pr", self.workload, "Smoke")
        attempt.execute(study, directory)
        row = load_json(directory / "run.json", 64 * 1024**2)
        row["worker"] = None  # in-process here; a real worker has exited
        (directory / "run.json").write_text(json.dumps(row))
        export_study(study, self.root / "flint.zip")
        (directory / "local-engine.json").write_text("{}")
        with self.assertRaisesRegex(Refusal, "local-engine.json"):
            export_study(study, self.root / "changed.zip")

    def test_recovery_refuses_while_the_owned_group_lives_and_accepts_its_absence(self):
        files = AttemptFiles(self.root / "run")
        session = files.session("smoke")
        session.process.mkdir(parents=True)
        intent = {"subject": "local", "executable": str(self.server), "listen": "127.0.0.1:12345"}
        session.intent.write_text(json.dumps(intent))
        alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(lambda: (alive.kill(), alive.wait()))
        status = {"schema": LOCAL_STATUS_SCHEMA, **intent, "state": "running", "safe_to_cleanup": False,
                  "process": {"pid": alive.pid, "pgid": alive.pid, "ps_lstart": "start"}}
        session.status.write_text(json.dumps(status))
        with self.assertRaisesRegex(Refusal, f"kill -TERM -{alive.pid}"):
            recovery.require_shutdown(files)
        alive.kill()
        alive.wait()
        recovery.require_shutdown(files)
        session.status.write_text(json.dumps({**status, "process": None}))
        with self.assertRaisesRegex(Refusal, "uncertain"):
            recovery.require_shutdown(files)
