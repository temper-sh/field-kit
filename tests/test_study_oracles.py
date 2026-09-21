"""Delivered answers are checked against the shipped, source-backed workload."""
import json
from pathlib import Path
import unittest

from fieldkit_runtime.experiments.qwen.method import grade

PACKAGE = Path(__file__).resolve().parents[1] / 'catalog/packages/qwen-machine-study@2'


class StudyOracleTest(unittest.TestCase):
    def test_oracle_accepts_json_semantics_and_rejects_plausible_wrong_artifacts(self):
        expected = {'site': 'North Annex', 'ticket': 'MA-017'}
        self.assertTrue(grade('{ "ticket": "MA-017", "site": "North Annex" }', expected)['correct'])
        for value in ('{"site":"North Annex","ticket":"MA-071"}', '{"site":"North Annex","ticket":"wrong","ticket":"MA-017"}', '```json\n{}\n```', '{}', '{"value":NaN}'):
            self.assertFalse(grade(value, expected)['correct'])
        self.assertFalse(grade('{"count":true}', {'count': 1})['correct'])

    def test_fixture_sources_independently_establish_registry_values_and_amendment(self):
        cases = {case['id']: case for case in json.loads((PACKAGE / 'workloads.json').read_bytes())['cases']}
        source = cases['registry']['prompt'].splitlines()
        facts = dict(line.split('=') for line in source if line.startswith('A') and '=' in line)
        for name in ('registry', 'registry-continue', 'registry-rewind', 'registry-return'):
            for key, value in cases[name]['expected'].items():
                self.assertEqual(value, facts[key])
        original = cases['notice']['expected']
        amended = cases['amend-notice']['expected']
        self.assertEqual([key for key in original if original[key] != amended[key]], ['end'])
        self.assertNotEqual(cases['other-registry']['expected'], cases['registry']['expected'])
