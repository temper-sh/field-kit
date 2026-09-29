from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime.catalog import Refusal, canonical_json
from fieldkit_runtime.execution import configure_execution, inspect_execution, validate_material
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
        with self.assertRaisesRegex(Refusal, r"Run \./setup\.sh"):
            inspect_execution(Path("/temper"), self.lock, runner=lambda *_: CommandResult(b"", b"unknown command", 2))

    def test_material_binds_the_inspected_lock_and_generation(self):
        execution = self.runner.execution(self.lock)
        material = {"schema": "temper-execution-material/v1", "execution": execution,
                    "generation": "b" * 64, "binding": "schema: temper-field-kit-binding/v1\n"}
        self.assertEqual(validate_material(canonical_json(material), execution), material)
        for key, value in (("generation", "unknown"), ("binding", ""), ("execution", {})):
            with self.subTest(key=key), self.assertRaises(Refusal):
                validate_material(canonical_json({**material, key: value}), execution)

    def test_configuration_refuses_old_host_and_mismatched_settings_without_fallback(self):
        settings = {"context_window_tokens": 32768, "max_output_tokens": 4096}
        good = {"schema": "temper-execution-configuration/v1", "preset": "fixture",
                "settings": settings, "context_execution_sha256": "c"*64}
        failures = [CommandResult(b"", b"unknown operation", 2)]
        for changed in ({"preset": "different"}, {"settings": {**settings, "max_output_tokens": 1}},
                        {"context_execution_sha256": ""}):
            failures.append(CommandResult(canonical_json({**good, **changed}), b"", 0))
        for response in failures:
            calls = []
            def run(argv, timeout):
                calls.append(argv)
                return response
            with self.subTest(response=response), self.assertRaises(Refusal):
                configure_execution("/temper", self.lock, "fixture", settings, self.root/"new.lock",
                                    dry_run=True, runner=run)
            self.assertEqual(len(calls), 1)
            self.assertIn("--dry-run", calls[0])
            self.assertEqual(list(self.root.iterdir()), [self.lock])
