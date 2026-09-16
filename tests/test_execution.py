from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime.catalog import Refusal
from fieldkit_runtime.execution import INPUTS, prepare_execution
from fieldkit_runtime.workflow import CommandFailure, CommandResult


class ExecutionPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = self.root / "execution.lock.json"
        # The adapter treats the lock as opaque bytes. Only Temper parses it.
        self.lock.write_bytes(b"opaque Temper-owned lock\n")
        self.destination = self.root / "derived"
        self.arguments = []
        self.files = {name: (name + "\n").encode() for name in INPUTS}
        self.response = {
            "schema": "temper-execution-inputs/v1", "profile": "qwen-local",
            "execution_digest": "a" * 64,
            "lock_sha256": hashlib.sha256(self.lock.read_bytes()).hexdigest(),
            "layouts": ["qwen-chat"], "changed": True, "dry_run": False,
            "inputs": {name: {"path": str(self.destination / name),
                               "sha256": hashlib.sha256(data).hexdigest()}
                       for name, data in self.files.items()},
        }

    def runner(self, arguments, timeout):
        self.arguments.append(list(arguments))
        self.assertEqual(timeout, 60)
        if "--dry-run" not in arguments:
            self.destination.mkdir(exist_ok=True)
            for name, data in self.files.items():
                (self.destination / name).write_bytes(data)
        return CommandResult(json.dumps(self.response).encode(), b"", 0)

    def prepare(self, **options):
        return prepare_execution(Path("/explicit/temper"), self.lock,
                                 self.destination, runner=self.runner, **options)

    def test_receives_only_public_primitive_and_verifies_every_exported_file(self):
        self.assertEqual(self.prepare(), self.response)
        self.assertEqual(self.arguments, [["/explicit/temper", "execution", "export",
                         "--lock", str(self.lock), "--out", str(self.destination), "--json"]])

    def test_dry_run_does_not_require_or_create_derived_files(self):
        self.response["dry_run"] = True
        self.prepare(dry_run=True)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.arguments[0][-1], "--dry-run")

    def test_refuses_unbound_or_incomplete_temper_response(self):
        original = copy.deepcopy(self.response)
        variants = [
            {"lock_sha256": "b" * 64}, {"execution_digest": "latest"},
            {"layouts": []}, {"layouts": ["qwen-chat", "qwen-chat"]},
            {"inputs": {}}, {"dry_run": True}, {"changed": "yes"},
            {"unexpected": True},
        ]
        for change in variants:
            with self.subTest(change=change):
                self.response = original | change
                with self.assertRaises(Refusal):
                    self.prepare()

    def test_refuses_escaped_path_and_changed_content(self):
        name = "manifest.yaml"
        self.response["inputs"][name]["path"] = str(self.root / name)
        with self.assertRaisesRegex(Refusal, "outside"):
            self.prepare()
        self.response["inputs"][name]["path"] = str(self.destination / name)
        self.files[name] = b"changed\n"
        with self.assertRaisesRegex(Refusal, "differs"):
            self.prepare()

    def test_failed_temper_command_is_not_usable_preparation(self):
        def failed(arguments, timeout):
            return CommandResult(b"", b"unsupported lock\n", 1)
        with self.assertRaises(CommandFailure):
            prepare_execution(Path("/explicit/temper"), self.lock, self.destination, runner=failed)
        self.assertFalse(self.destination.exists())

    def test_refuses_symlink_lock_before_calling_temper(self):
        link = self.root / "alias"
        link.symlink_to(self.lock)
        with self.assertRaises(Refusal):
            prepare_execution(Path("/explicit/temper"), link, self.destination, runner=self.runner)
        self.assertFalse(self.arguments)

    def test_normalizes_parent_segments_without_resolving_a_final_symlink(self):
        lock = self.root / "missing" / ".." / self.lock.name
        result = prepare_execution(Path("/explicit/temper"), lock, self.destination, runner=self.runner)
        self.assertEqual(result["lock_sha256"], self.response["lock_sha256"])


if __name__ == "__main__":
    unittest.main()
