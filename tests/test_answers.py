from __future__ import annotations

import unittest

from fieldkit_runtime.answers import validate_answer_fields, validate_answers
from fieldkit_runtime.catalog import Refusal


class AnswerContractTest(unittest.TestCase):
    def test_all_explicit_answer_states_are_accepted(self) -> None:
        fields = validate_answer_fields(["context", "fit", "task"])
        answers = validate_answers({
            "context": {"state": "unknown", "reason": "not measured"},
            "fit": {"state": "observed", "value": {"ready": True}},
            "task": {"state": "failed", "reason": "oracle mismatch", "value": {"score": 0}},
        }, fields)
        self.assertEqual(answers["context"]["state"], "unknown")
        self.assertEqual(answers["task"]["value"], {"score": 0})

    def test_omitted_answer_is_refused(self) -> None:
        with self.assertRaisesRegex(Refusal, "exactly cover"):
            validate_answers({
                "fit": {"state": "observed", "value": True},
            }, ["fit", "task"])

    def test_non_observed_answer_requires_a_reason(self) -> None:
        with self.assertRaisesRegex(Refusal, "requires a reason"):
            validate_answers({
                "context": {"state": "unknown"},
            }, ["context"])


if __name__ == "__main__":
    unittest.main()
