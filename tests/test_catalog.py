from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from fieldkit_runtime.catalog import QuestionCatalog, Refusal, canonical_json, digest, load_question_material, parse_machine_facts
from tests.fixtures import question_entry, write_question_catalog

ROOT = Path(__file__).resolve().parents[1]

def facts_bytes(memory=34359738368):
    return f'''schema: temper-machine-facts/v1
target:
  os: darwin
  arch: arm64
  distribution: macos
  distribution_version: 26.0
hardware_model: MacFixture1,1
chip: Apple M fixture
os_build: TESTBUILD
physical_memory_bytes: {memory}
metal_device_memory_mib: 26542
metal_device_memory_source: predicted-metal-81-percent
wired_limit_mib: 24576
wired_limit_source: live-sysctl
'''.encode()

class QuestionCatalogTest(unittest.TestCase):
    def test_complete_question_and_prepared_visibility(self):
        for state in ('active', 'qualifying', 'suspended'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                catalog = QuestionCatalog.load(write_question_catalog(Path(temporary), availability=state))
                self.assertEqual(len(catalog.active()), int(state == 'active'))
                self.assertEqual(len(catalog.qualifying()), int(state == 'qualifying'))
                self.assertEqual(catalog.questions[0].package['schema'], 'field-kit-question-package/v2')

    def test_shipped_preparation_is_not_active(self):
        catalog = QuestionCatalog.load(ROOT/'catalog/questions.json')
        self.assertEqual(catalog.active(), ())
        self.assertTrue(catalog.qualifying())

    def test_execution_lock_tampering_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = write_question_catalog(Path(temporary))
            (path.parent/'packages/fixture-question/execution.lock.json').write_bytes(b'changed')
            with self.assertRaisesRegex(Refusal, 'hash mismatch|identity|hash'):
                QuestionCatalog.load(path)

    def test_legacy_runtime_facts_and_duplicated_stages_are_refused(self):
        for field, value in [('software', {}), ('stages', [])]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                path = write_question_catalog(Path(temporary))
                package_path = path.parent/'packages/fixture-question/package.json'
                package = json.loads(package_path.read_bytes())
                if field == 'software': package[field] = value
                else: package['mechanics'][field] = value
                package_path.write_bytes(canonical_json(package))
                with self.assertRaises(Refusal): load_question_material(package_path)

    def test_exact_memory_does_not_round(self):
        entry = question_entry()
        entry.package['applicability']['physical_memory_bytes'] = 34359738368
        self.assertTrue(entry.applicable(parse_machine_facts(facts_bytes()))[0])
        self.assertFalse(entry.applicable(parse_machine_facts(facts_bytes(34359738369)))[0])

    def test_incomplete_machine_with_extra_fields_is_refused(self):
        raw = facts_bytes().replace(b'os_build: TESTBUILD\n', b'unrelated: value\n')
        with self.assertRaises(Refusal): parse_machine_facts(raw)

    def test_unknown_question_is_refused(self):
        with self.assertRaisesRegex(Refusal, 'unknown Field Kit question'):
            QuestionCatalog.load(ROOT/'catalog/questions.json').find('absent@1')

    def test_invalid_outcome_restrictions_are_refused_before_planning(self):
        for outcomes in ([], ["erase"], [{}], ["restore", "restore"], ["restore", "keep"]):
            with self.subTest(outcomes=outcomes), tempfile.TemporaryDirectory() as temporary:
                path = write_question_catalog(Path(temporary))
                package_path = path.parent/'packages/fixture-question/package.json'
                package = json.loads(package_path.read_bytes())
                package['consent']['allowed_outcomes'] = outcomes
                package_path.write_bytes(canonical_json(package))
                with self.assertRaisesRegex(Refusal, 'allowed outcomes'):
                    load_question_material(package_path)
