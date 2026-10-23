"""A stopped study can recover through fresh Temper evidence without erasing its failure."""
import dataclasses
from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime.common import Refusal, canonical_json, digest, load_json
from fieldkit_runtime.process import atomic_write
from fieldkit_runtime.records import AttemptFiles
from fieldkit_runtime.study import Study
from tests.test_research import FakeTemper, fake_context


class RecoveryTemper(FakeTemper):
    available = True
    failure = None
    wrong_digest = False

    def verify_stopped(self, path):
        if not self.available:
            raise Refusal("Temper needs execution verify-stopped")
        if self.failure:
            raise Refusal(self.failure)
        status = load_json(path)
        return {"schema": "temper-probe-recovery/v1", "status_sha256": "0" * 64 if self.wrong_digest else digest(path.read_bytes()),
                **{key: status[key] for key in ("root", "installation", "generation")},
                "safe_to_cleanup": True, "verified_at": "2026-10-23T18:00:00Z",
                "process_ids": [72471, 72472, 72486], "process_group_ids": [72472, 72486],
                "listeners": [status["listen"], "127.0.0.1:32497"], "temper": self.identity()}


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.study = Study(self.root, "muse", dataclasses.replace(fake_context(), temper=RecoveryTemper))
        self.files = AttemptFiles(self.study.directory / "runs/first")
        self.session = self.files.session("populated")
        self.original_run = {"id": "first", "state": "retained", "safe_to_cleanup": False,
                             "worker": None, "error": {"kind": "resource-stop", "message": "old producer"},
                             "temper": {"path": "/original/temper"}}
        self.files.save_result(self.original_run)
        self.study.save({"schema": "field-kit-study/v3", "id": "muse", "state": "open", "runs": ["first"],
                         "temper": {"path": "/original/temper"}})
        (self.study.directory / ".lock").touch()
        self.intent = {"root": "/owned/muse", "installation": "field-kit-test", "generation": "c" * 64,
                       "listen": "127.0.0.1:62626"}
        atomic_write(self.session.intent, canonical_json(self.intent))
        self.status = {"schema": "temper-probe-status/v1", **self.intent, "state": "stopped", "safe_to_cleanup": False,
                       "error": "untracked orphan", "temper_pid": 72471, "process_group_id": 72472,
                       "roles": [{"id": "router", "pid": 72472, "pgid": 72472}, {"id": "engine", "pid": 72486, "pgid": 72486}]}
        atomic_write(self.session.status, canonical_json(self.status))
        RecoveryTemper.available, RecoveryTemper.failure, RecoveryTemper.wrong_digest = True, None, False

    def test_recovers_without_rewriting_original_status_result_or_temper_identity(self):
        original_status, original_result = self.session.status.read_bytes(), self.files.result.read_bytes()
        result = self.study.recover(Path("/repair/temper"))
        self.assertEqual(result["recovered"], ["first"])
        self.study.require_idle()
        self.assertEqual(self.session.status.read_bytes(), original_status)
        self.assertEqual(self.files.result.read_bytes(), original_result)
        current = self.files.load_run()
        self.assertEqual(current["temper"], self.original_run["temper"])
        self.assertEqual(current["error"], self.original_run["error"])
        recovery = load_json(self.session.recovery)
        self.assertEqual(recovery["temper"]["path"], "/repair/temper")
        self.assertEqual(recovery["status_sha256"], digest(original_status))
        self.assertEqual(self.study.recover()["recovered"], [])

    def test_restarts_after_recovery_observation_was_saved_before_run_commit(self):
        observation = RecoveryTemper(Path("/repair/temper")).verify_stopped(self.session.status)
        atomic_write(self.session.recovery, canonical_json(observation))
        RecoveryTemper.available = False
        self.assertEqual(self.study.recover()["recovered"], ["first"])

    def test_failed_verification_or_missing_capability_leaves_attempt_retained(self):
        original = self.files.run.read_bytes()
        for available, failure, wrong_digest in [(False, None, False), (True, "group still exists", False), (True, None, True)]:
            with self.subTest(available=available, failure=failure, wrong_digest=wrong_digest):
                RecoveryTemper.available, RecoveryTemper.failure, RecoveryTemper.wrong_digest = available, failure, wrong_digest
                expected = "verify-stopped" if not available else "group still exists" if failure else "does not match"
                with self.assertRaisesRegex(Refusal, expected):
                    self.study.recover()
                self.assertEqual(self.files.run.read_bytes(), original)
                self.assertFalse(self.session.recovery.exists())

    def test_other_session_proof_and_changed_original_status_are_not_reusable(self):
        observation = RecoveryTemper().verify_stopped(self.session.status)
        atomic_write(self.session.recovery, canonical_json(observation))
        for change in ({"error": "changed"}, {"generation": "d" * 64}, {"state": "running"}):
            with self.subTest(change=change):
                atomic_write(self.session.status, canonical_json({**self.status, **change}))
                with self.assertRaises(Refusal):
                    self.study.recover()
                self.assertFalse(self.files.load_run()["safe_to_cleanup"])
