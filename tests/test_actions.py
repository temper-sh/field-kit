from __future__ import annotations

import unittest

from fieldkit_runtime.actions import (
    initial_action_request,
    propose_action,
    validate_investigation,
)
from fieldkit_runtime.catalog import Refusal


def adaptive_investigation() -> dict:
    return {
        "schema": "field-kit-investigation/v2",
        "parameters": [
            {
                "id": "cache-format",
                "type": "enum",
                "choices": ["q4", "q8"],
                "default": "q8",
            },
            {
                "id": "target-context-tokens",
                "type": "integer",
                "minimum": 8192,
                "maximum": 262144,
                "step": 1,
                "default": 98304,
            },
        ],
        "actions": [
            {
                "id": "final-context-validation",
                "kind": "final-validation",
                "parameters": ["cache-format", "target-context-tokens"],
                "attempts_max": 1,
                "runtime_minutes_max": 2,
                "evidence_bytes_max": 1048576, "process_watch": None,
            },
            {
                "id": "measure-context",
                "kind": "measurement",
                "parameters": ["cache-format", "target-context-tokens"],
                "attempts_max": 5,
                "runtime_minutes_max": 2,
                "evidence_bytes_max": 1048576, "process_watch": None,
            },
        ],
        "initial_action": {
            "id": "measure-context",
            "parameters": {
                "cache-format": "q8",
                "target-context-tokens": 98304,
            },
        },
        "final_validation_action": "final-context-validation",
        "action_selection": "operator-bounded",
        "total_attempts_max": 6,
        "total_runtime_minutes_max": 12,
    }


class ActionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.investigation = adaptive_investigation()
        validate_investigation(self.investigation, "bounded-adaptive")

    def test_arbitrary_approved_integer_targets_use_the_same_action(self) -> None:
        first = propose_action(self.investigation, {
            "id": "measure-context",
            "parameters": {
                "cache-format": "q8",
                "target-context-tokens": 98304,
            },
            "reason": "initial boundary point",
        }, [])
        second = propose_action(self.investigation, {
            "id": "measure-context",
            "parameters": {
                "cache-format": "q8",
                "target-context-tokens": 200000,
            },
            "reason": "probe a higher point",
        }, [])
        self.assertEqual(first.document["parameters"]["target-context-tokens"], 98304)
        self.assertEqual(second.document["parameters"]["target-context-tokens"], 200000)
        self.assertEqual(
            second.document["changes"]["target-context-tokens"],
            {"from": None, "to": 200000},
        )
        self.assertNotEqual(first.sha256, second.sha256)

    def test_target_outside_the_approved_integer_range_is_refused(self) -> None:
        with self.assertRaisesRegex(Refusal, "outside the approved range"):
            propose_action(self.investigation, {
                "id": "measure-context",
                "parameters": {
                    "cache-format": "q8",
                    "target-context-tokens": 300000,
                },
                "reason": "try an undeclared target",
            }, [])

    def test_question_can_bind_an_integer_lattice_without_hard_coding_targets(self) -> None:
        target = self.investigation["parameters"][1]
        target.update({
            "minimum": 65536,
            "maximum": 122880,
            "step": 8192,
            "default": 98304,
        })
        with self.assertRaisesRegex(Refusal, "integer lattice"):
            propose_action(self.investigation, {
                "id": "measure-context",
                "parameters": {
                    "cache-format": "q8",
                    "target-context-tokens": 100000,
                },
                "reason": "off lattice",
            }, [])
        action = propose_action(self.investigation, {
            "id": "measure-context",
            "parameters": {
                "cache-format": "q8",
                "target-context-tokens": 106496,
            },
            "reason": "next lattice point",
        }, [])
        self.assertEqual(action.document["parameters"]["target-context-tokens"], 106496)

    def test_boolean_is_not_accepted_as_an_integer_target(self) -> None:
        with self.assertRaisesRegex(Refusal, "must be an integer"):
            propose_action(self.investigation, {
                "id": "measure-context",
                "parameters": {
                    "cache-format": "q8",
                    "target-context-tokens": True,
                },
                "reason": "malformed proposal",
            }, [])

    def test_per_action_attempt_ceiling_is_enforced(self) -> None:
        attempts = [
            {"kind": "question-action", "action": {"id": "measure-context"}}
            for _ in range(5)
        ]
        with self.assertRaisesRegex(Refusal, "measure-context.*exhausted"):
            propose_action(self.investigation, {
                "id": "measure-context",
                "parameters": {
                    "cache-format": "q4",
                    "target-context-tokens": 131072,
                },
                "reason": "one attempt too many",
            }, attempts)

    def test_initial_action_is_a_normal_validatable_request(self) -> None:
        request = initial_action_request(self.investigation)
        action = propose_action(self.investigation, request, [])
        self.assertEqual(action.document["attempt"], 1)
        self.assertEqual(action.document["id"], "measure-context")

    def test_fixed_question_cannot_hide_a_tuning_range(self) -> None:
        with self.assertRaisesRegex(Refusal, "fixed questions cannot"):
            validate_investigation(self.investigation, "fixed")

    def test_action_runtime_cannot_exceed_total_runtime(self) -> None:
        self.investigation["actions"][0]["runtime_minutes_max"] = 13
        with self.assertRaisesRegex(Refusal, "exceeds the total runtime ceiling"):
            validate_investigation(self.investigation, "bounded-adaptive")




    def test_adaptive_initial_action_cannot_be_its_final_validation(self) -> None:
        self.investigation["initial_action"] = {
            "id": "final-context-validation",
            "parameters": {
                "cache-format": "q8",
                "target-context-tokens": 98304,
            },
        }
        with self.assertRaisesRegex(Refusal, "one distinct final-validation action"):
            validate_investigation(self.investigation, "bounded-adaptive")

if __name__ == "__main__":
    unittest.main()
