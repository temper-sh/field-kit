from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime.catalog import Refusal, canonical_json
from fieldkit_runtime.execution import inspect_execution, validate_material
from fieldkit_runtime.workflow import CommandResult
from tests.test_workflow import FakeRunner


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / "execution.lock.json"
        self.lock.write_bytes(b"opaque Temper fixture lock\n")
        self.runner = FakeRunner()

    def test_inspection_is_read_only_and_passes_one_opaque_lock(self):
        value = inspect_execution(Path("/explicit/temper"), self.lock, runner=self.runner)
        self.assertEqual(list(self.root.iterdir()), [self.lock])
        self.assertEqual(self.runner.calls, [["/explicit/temper", "execution", "inspect", "--lock", str(self.lock)]])
        self.assertEqual(value["profile"], "fixture")

    def test_incomplete_or_unbound_response_is_refused(self):
        original = self.runner.execution(self.lock)
        for change in ({"lock_sha256": "b" * 64}, {"layouts": []}, {"execution_digest": "latest"},
                       {"request_defaults": {}}, {"unexpected": True}):
            response = {**original, **change}
            with self.subTest(change=change), self.assertRaises(Refusal):
                inspect_execution(Path("/temper"), self.lock,
                    runner=lambda *_: CommandResult(canonical_json(response), b"", 0))

    def test_symlink_lock_refused_before_host_call(self):
        link = self.root / "alias"; link.symlink_to(self.lock)
        with self.assertRaises(Refusal): inspect_execution(Path("/temper"), link, runner=self.runner)
        self.assertFalse(self.runner.calls)

    def test_old_host_has_actionable_refusal(self):
        with self.assertRaisesRegex(Refusal, "matching development build"):
            inspect_execution(Path("/temper"), self.lock, runner=lambda *_: CommandResult(b"", b"unknown command", 2))

    def test_material_binds_the_inspected_lock_and_generation(self):
        execution = self.runner.execution(self.lock)
        material = {"schema": "temper-execution-material/v1", "execution": execution,
                    "generation": "b" * 64, "binding": "schema: temper-field-kit-binding/v1\n"}
        self.assertEqual(validate_material(canonical_json(material), execution), material)
        for key, value in (("generation", "unknown"), ("binding", ""), ("execution", {})):
            with self.subTest(key=key), self.assertRaises(Refusal):
                validate_material(canonical_json({**material, key: value}), execution)
