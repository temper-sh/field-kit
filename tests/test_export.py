"""A contributor can return complete evidence without caches or lost failures."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from fieldkit_runtime.common import Refusal
from fieldkit_runtime.export import export_study
from fieldkit_runtime.process import file_digest
from fieldkit_runtime.study import STUDY_SCHEMA


class ExportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.study = self.root / "studies/q4"
        self.study.mkdir(parents=True)
        (self.study / ".lock").touch()
        self.state = {"schema": STUDY_SCHEMA, "id": "q4", "state": "closed",
                      "runs": [{"id": "run-1", "candidate": "rapid"}]}
        (self.study / "study.json").write_text(json.dumps(self.state))
        (self.study / "report.md").write_text("Failed first attempt; quality unassessed.")
        self.run = self.root / "runs/run-1"
        self.run.mkdir(parents=True)
        (self.run / ".lock").touch()
        (self.run / "execution.lock.json").write_text('{"exact":"material"}')
        result = {"id": "run-1", "study": {"id": "q4", "candidate": "rapid"},
                  "lock_sha256": file_digest(self.run / "execution.lock.json"),
                  "state": "complete", "workload": {"kind": "coding"},
                  "cases": [{"id": "async-stream", "status": "runtime-failure"}]}
        for name in ("run.json", "result.json"):
            (self.run / name).write_text(json.dumps(result))
        (self.run / "report.md").write_text("Timed out before submitting.")
        case = self.run / "cases/async-stream/turn-01"
        case.mkdir(parents=True)
        (case / "response.json").write_text('{"failure":"request-timeout"}')
        self.destination = self.root / "returned.zip"

    def test_complete_export_preserves_failed_turns_and_existing_reviews_without_weights(self):
        (self.study / "conclusions.md").write_text("Quality remains unknown.")
        (self.study / "hf-cache").mkdir()
        (self.study / "hf-cache/weight").write_bytes(b"not evidence")
        (self.run / "review").mkdir()
        (self.run / "review/tests.json").write_text('{"status":"grading-failed"}')
        (self.run / "review/fixture").mkdir()
        (self.run / "review/fixture/source.py").write_text("reconstructible")
        export_study(self.root, "q4", self.destination)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("studies/q4/conclusions.md", archive.namelist())
            self.assertEqual(archive.read("runs/run-1/cases/async-stream/turn-01/response.json"),
                             b'{"failure":"request-timeout"}')
            self.assertIn("runs/run-1/review/tests.json", archive.namelist())
            self.assertFalse(any("hf-cache" in name or "/fixture/" in name for name in archive.namelist()))

    def test_missing_referenced_run_does_not_publish_a_partial_archive(self):
        self.state["runs"].append({"id": "run-missing", "candidate": "splash"})
        (self.study / "study.json").write_text(json.dumps(self.state))
        with self.assertRaisesRegex(Refusal, "referenced run"):
            export_study(self.root, "q4", self.destination)
        self.assertFalse(self.destination.exists())

    def test_missing_case_or_external_symlink_is_not_silently_exported(self):
        case = self.run / "cases/async-stream"
        case.rename(case.with_name("saved"))
        with self.assertRaisesRegex(Refusal, "case evidence"):
            export_study(self.root, "q4", self.destination)
        case.symlink_to(case.with_name("saved"), target_is_directory=True)
        with self.assertRaisesRegex(Refusal, "case evidence"):
            export_study(self.root, "q4", self.destination)
        self.assertFalse(self.destination.exists())

    def test_export_needs_closed_state_and_never_overwrites_an_existing_file(self):
        self.state["state"] = "open"
        (self.study / "study.json").write_text(json.dumps(self.state))
        with self.assertRaisesRegex(Refusal, "close the study"):
            export_study(self.root, "q4", self.destination)
        self.destination.write_bytes(b"original")
        with self.assertRaisesRegex(Refusal, "already exists"):
            export_study(self.root, "q4", self.destination)
        self.assertEqual(self.destination.read_bytes(), b"original")
