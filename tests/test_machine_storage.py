"""Exclusive machine, Temper's own checks, study root and weights storage."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dataclasses import replace

from fieldkit_runtime import attempt, storage
from fieldkit_runtime.approval import preview, summary
from fieldkit_runtime.common import Refusal, canonical_json, load_json
from fieldkit_runtime.lockview import installation
from fieldkit_runtime.process import atomic_write
from fieldkit_runtime.study import Study
from tests.test_research import GIB, FakeTemper, fake_context, fake_volume, lock

REVISION = "1" * 40

def sourced_lock(name="research-model", files=(("q5.gguf", 2048, "5" * 64),), release="fixed"):
    document = lock(name)
    document["records"]["artifacts"]["weights"] = {
        "sources": [{"hub": "huggingface", "repo": "owner/model", "revision": REVISION}],
        "files": [{"path": path, "bytes": size, "sha256": sha} for path, size, sha in files]}
    document["records"]["engines"]["engine"]["release"] = release
    return document

def cached(cache, path, sha, data, link=True):
    """A file in Hugging Face cache layout: a blob plus a snapshot link, or an exFAT-style copy."""
    repository = cache / "models--owner--model"
    snapshot = repository / "snapshots" / REVISION / path
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if link:
        blob = repository / "blobs" / sha
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)
        snapshot.symlink_to(os.path.relpath(blob, snapshot.parent))
    else:
        snapshot.write_bytes(data)
    return snapshot

class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cache = self.root / "hf-cache"
        self.cache.mkdir()
        self.brief = self.root / "brief.md"
        self.brief.write_text("Find useful presets on this machine.")
        self.workload = self.root / "workload.json"
        self.workload.write_bytes(canonical_json({"question": "Smoke", "request": {
            "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 8}}))
        FakeTemper.reset()
        self.volume = fake_volume
        self.context = fake_context()

    def write_lock(self, document, name="candidate.lock.json"):
        path = self.root / name
        path.write_bytes(canonical_json(document))
        return path

    def preview(self, locks, **options):
        context = replace(self.context, volume=lambda path: self.volume(path))
        return preview(FakeTemper(), locks, self.brief, **{"weights_cache": self.cache, "context": context, **options})

    def open_study(self, locks, name="study", **options):
        study = Study(self.root, name, self.context)
        document = self.preview(locks, hours=1, download_gib=1, root=study.runtime(), **options)
        study.initialize(document, FakeTemper())
        return study

    def execute(self, study, directory):
        def call(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            kwargs["stream_reader"].raw_path.write_text("raw")
            return {"message": {"content": "OK", "reasoning": "", "tool_calls": []},
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1}, "service_seconds": 0.1,
                    "first_token_seconds": 0.05, "first_answer_seconds": 0.05, "finish_reason": "stop",
                    "stream_complete": True}
        study.context = fake_context(call, 2 * GIB)
        return attempt.execute(study, directory)

    def finish(self, directory):
        row = load_json(directory / "run.json", 64 * 1024**2)
        row.update(state="complete", safe_to_cleanup=True)
        atomic_write(directory / "run.json", canonical_json(row))

    def calls(self, kind):
        return [call for call in FakeTemper.calls if call[0] == kind]

class ExclusiveMachineTest(Base):
    def test_running_layout_is_stopped_restopped_after_a_pause_and_restored_once(self):
        FakeTemper.layout["id"] = "default"
        study = self.open_study([self.write_lock(lock())])
        state = study.load()
        self.assertTrue(state["user_layout"]["stopped"])
        self.assertIsNone(FakeTemper.layout["id"])
        self.assertIn("temper layout activate default", (study.directory / "restore.md").read_text())
        FakeTemper.layout["id"] = "default"  # The tester used their AI during a pause.
        self.finish(study.reserve("research-model", self.workload, "Smoke after a pause"))
        self.assertIsNone(FakeTemper.layout["id"])
        self.assertEqual(len(self.calls("stop")), 2)
        self.assertEqual(study.close()["user_layout"], "restored: temper layout activate default")
        self.assertEqual(FakeTemper.layout["id"], "default")
        study.close()
        self.assertEqual(len(self.calls("activate")), 1)

    def test_nothing_running_means_nothing_stopped_or_started(self):
        study = self.open_study([self.write_lock(lock())])
        self.finish(study.reserve("research-model", self.workload, "Smoke"))
        self.assertEqual(study.close()["user_layout"], "not stopped by this study")
        self.assertEqual(self.calls("stop") + self.calls("activate"), [])
        self.assertIn("did not stop a Temper layout", (study.directory / "restore.md").read_text())

    def test_unapproved_layout_refuses_attempt_without_stopping_it(self):
        study = self.open_study([self.write_lock(lock())])
        FakeTemper.layout["id"] = "writing"
        with self.assertRaisesRegex(Refusal, "not approved"):
            study.reserve("research-model", self.workload, "Smoke")
        self.assertEqual(FakeTemper.layout["id"], "writing")
        self.assertEqual(self.calls("stop"), [])
        self.assertEqual(study.load()["runs"], [])

    def test_busy_layout_keeps_approval_and_the_next_attempt_stops_it(self):
        FakeTemper.layout.update(id="default", busy=True)
        study = self.open_study([self.write_lock(lock())])
        self.assertIn("could not stop", study.load()["user_layout"]["stop_error"])
        with self.assertRaisesRegex(Refusal, "requests finish"):
            study.reserve("research-model", self.workload, "Smoke")
        FakeTemper.layout["busy"] = False
        study.reserve("research-model", self.workload, "Smoke")
        self.assertNotIn("stop_error", study.load()["user_layout"])

    def test_failed_restore_still_closes_and_names_the_command(self):
        FakeTemper.layout["id"] = "default"
        study = self.open_study([self.write_lock(lock())], user_root=self.root / "my temper")
        FakeTemper.layout["fail_activate"] = True
        result = study.close()
        self.assertEqual(result["state"], "closed")
        self.assertIn("run: temper layout activate default --root", result["user_layout"])
        self.assertIn("'", result["user_layout"])  # A root with a space stays one argument.
        self.assertIn("activation pending", study.load()["user_layout"]["restore_error"])

class TemperChecksTest(Base):
    def authoring(self):
        root = self.root / "authoring"
        (root / "presets.d").mkdir(parents=True, exist_ok=True)
        (root / "presets.d" / "research-model.json").write_text("{}")
        return root

    def test_temper_check_and_plan_use_the_study_weights_cache(self):
        document = self.preview([self.write_lock(lock())], hours=1, download_gib=1, authoring_root=self.authoring())
        assessment = summary(document)["candidates"][0]["temper_assessment"]
        self.assertTrue(assessment["check"]["valid"])
        self.assertTrue(assessment["plan"]["can_prepare"])
        self.assertEqual({call[3] for call in self.calls("check") + self.calls("plan")}, {str(self.cache)})

    def test_temper_refusal_stops_the_plan_and_missing_plan_is_disclosed(self):
        FakeTemper.check = {"valid": True, "refusals": ["needs 40 GiB of Metal memory"]}
        with self.assertRaisesRegex(Refusal, "40 GiB"):
            self.preview([self.write_lock(lock())], authoring_root=self.authoring())
        FakeTemper.reset()
        FakeTemper.plan_supported = False
        document = self.preview([self.write_lock(lock())], authoring_root=self.authoring())
        self.assertIn("unavailable", document["temper_assessment"]["research-model"]["plan"])

    def test_lock_without_an_authoring_preset_keeps_known_sizes(self):
        document = self.preview([self.write_lock(lock())], authoring_root=self.root / "empty-authoring")
        self.assertEqual(document["temper_assessment"]["research-model"]["check"], None)
        self.assertEqual(self.calls("check"), [])

class WeightsStorageTest(Base):
    def test_cache_files_present_before_are_not_downloads_and_hf_home_stays(self):
        cached(self.cache, "q4.gguf", "4" * 64, b"q" * 1024)
        document = sourced_lock(files=(("q4.gguf", 1024, "4" * 64), ("q5.gguf", 2048, "5" * 64)))
        study = self.open_study([self.write_lock(document)])
        rows = summary(self.preview([self.write_lock(document)]))
        self.assertEqual([row["path"] for row in rows["weights_to_download"]], ["q5.gguf"])
        directory = study.reserve("research-model", self.workload, "Smoke")
        with patch.dict(os.environ, {"HF_HOME": "/users/own/hf-home"}):
            self.execute(study, directory)
        prepare = self.calls("prepare")[0]
        self.assertEqual(prepare[2:], (str(self.cache), "/users/own/hf-home"))
        self.assertEqual(load_json(directory / "run.json", 64 * 1024**2)["storage"]["weights_cache"]["path"], str(self.cache))

    def test_close_removes_exact_study_added_files_and_keeps_the_users(self):
        users = cached(self.cache, "q4.gguf", "4" * 64, b"q" * 1024)
        document = sourced_lock(files=(("q4.gguf", 1024, "4" * 64), ("q5.gguf", 2048, "5" * 64),
                                       ("q6.gguf", 3072, "6" * 64), ("q8.gguf", 4096, "8" * 64)))
        study = self.open_study([self.write_lock(document)])
        added = cached(self.cache, "q5.gguf", "5" * 64, b"5" * 2048)
        copy_layout = cached(self.cache, "q6.gguf", "6" * 64, b"6" * 3072, link=False)
        changed = cached(self.cache, "q8.gguf", "8" * 64, b"8" * 10, link=False)
        result = study.close()["weights"]
        self.assertFalse(added.exists() or added.is_symlink())
        self.assertFalse((self.cache / "models--owner--model/blobs" / ("5" * 64)).exists())
        self.assertFalse(copy_layout.exists())
        self.assertTrue(changed.exists())
        self.assertEqual(result["kept"][0]["reason"], "snapshot size differs from the lock")
        self.assertEqual(users.read_bytes(), b"q" * 1024)

    def test_blob_shared_with_another_revision_stays(self):
        document = sourced_lock()
        study = self.open_study([self.write_lock(document)])
        added = cached(self.cache, "q5.gguf", "5" * 64, b"5" * 2048)
        other = self.cache / "models--owner--model/snapshots" / ("2" * 40) / "q5.gguf"
        other.parent.mkdir(parents=True)
        other.symlink_to(os.path.relpath(self.cache / "models--owner--model/blobs" / ("5" * 64), other.parent))
        result = study.close()["weights"]
        self.assertFalse(added.is_symlink())
        self.assertEqual(other.read_bytes(), b"5" * 2048)
        self.assertEqual(result["kept"][0]["reason"], "blob used by another revision")

    def test_keep_policy_removes_nothing(self):
        study = self.open_study([self.write_lock(sourced_lock())], weights_cleanup="keep")
        added = cached(self.cache, "q5.gguf", "5" * 64, b"5" * 2048)
        self.assertEqual(study.close()["weights"], "kept: " + str(self.cache))
        self.assertTrue(added.exists())

    def test_disconnected_cache_is_refused_before_download(self):
        with self.assertRaisesRegex(Refusal, "Connect its disk"):
            self.preview([self.write_lock(lock())], weights_cache=self.root / "Volumes/SSD/hub")
        study = self.open_study([self.write_lock(lock())])
        self.cache.rmdir()
        with self.assertRaisesRegex(Refusal, "unavailable"):
            study.reserve("research-model", self.workload, "Smoke")

    def test_disconnect_after_reservation_fails_the_attempt_as_storage_without_temper(self):
        study = self.open_study([self.write_lock(lock())])
        directory = study.reserve("research-model", self.workload, "Smoke")
        self.cache.rmdir()
        self.execute(study, directory)
        result = load_json(directory / "result.json", 64 * 1024**2)
        self.assertEqual((result["state"], result["error"]["kind"]), ("failed", "storage"))
        self.assertEqual(self.calls("prepare"), [])

    def test_non_apfs_root_and_short_disk_are_refused(self):
        def volume(path, filesystem="apfs", free=500 * GIB):
            return {**fake_volume(path), "filesystem": filesystem, "free_bytes": free}
        study = Study(self.root, "refused")
        self.volume = lambda path: volume(path, "exfat")
        with self.assertRaisesRegex(Refusal, "APFS"):
            self.preview([self.write_lock(lock())], root=study.runtime())
        # exFAT may keep full snapshot copies: 2048 absent bytes need 4096 on the cache volume.
        self.volume = lambda path: (volume(path, "exfat", 3000) if Path(path) == self.cache
                                                else {**volume(path), "mount_point": "/internal"})
        with self.assertRaisesRegex(Refusal, "not enough free disk"):
            self.preview([self.write_lock(sourced_lock())], root=study.runtime())
        self.volume = lambda path: volume(path, free=storage.INTERNAL_RESERVE_BYTES - 1)
        with self.assertRaisesRegex(Refusal, "10 GiB reserve"):
            self.preview([self.write_lock(lock())], root=study.runtime())

class StudyRootTest(Base):
    def test_shared_engine_shares_an_installation_and_close_removes_each_once(self):
        locks = [self.write_lock(sourced_lock("model-a"), "a.json"), self.write_lock(sourced_lock("model-b"), "b.json"),
                 self.write_lock(sourced_lock("model-c", release="other"), "c.json")]
        names = [installation(load_json(path)) for path in locks]
        self.assertEqual(names[0], names[1])
        self.assertNotEqual(names[0], names[2])
        study = self.open_study(locks)
        for candidate in ("model-a", "model-b", "model-c"):
            directory = study.reserve(candidate, self.workload, "Smoke")
            self.execute(study, directory)
        self.assertEqual({call[1] for call in self.calls("prepare")}, set(names))
        self.assertTrue(all(load_json(study.path_for(run) / "run.json")["installation"] in names for run in study.load()["runs"]))
        study.close()
        self.assertEqual(sorted(call[1] for call in self.calls("remove")), sorted(set(names)))
        self.assertFalse((study.directory / ".runtime").exists())

class VolumeFactsTest(unittest.TestCase):
    def test_mount_lines_with_spaces_name_the_filesystem(self):
        output = ("/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled)\n"
                  "/dev/disk4s2 on /Volumes/Crucial X10 (exfat, local, nodev, nosuid, noowners)\n")
        with patch.object(storage.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, output, "")):
            self.assertEqual(storage._mounts()[1], ("/dev/disk4s2", "/Volumes/Crucial X10", "exfat"))

    def test_real_volume_facts_are_read_only_and_never_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            record = storage.describe(Path(temp) / "planned" / "root")
            self.assertFalse(record["exists"])
            self.assertIsInstance(record["free_bytes"], int)
            self.assertFalse((Path(temp) / "planned").exists())
            self.assertIn(record["filesystem"], ("apfs", None) if os.uname().sysname == "Darwin" else (None,))
