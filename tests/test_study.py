"""The study must produce comparable rows and conservative tuning conclusions."""
import copy
import unittest

from fieldkit_runtime.experiments.qwen.method import choose_candidate, confirm_candidate, context_summary, performance_summary, variant_records


def observation(seconds=100, *, identity="baseline", correct=True, valid=True):
    return {"configuration": identity, "valid": valid, "correct": correct,
            "cases": [{"id": "document", "correct": correct, "service_seconds": seconds,
                       "prefill_tokens_per_second": 120.0, "generation_tokens_per_second": 9.0,
                       "first_token_seconds": 2.0, "first_answer_seconds": 3.0,
                       "usage": {"prompt_tokens": 1024, "completion_tokens": 64}}],
            "resources": {"roles": {"engine": {"rss_bytes_max": 20 * 1024**3}}, "swap_growth_bytes": 0}}


class StudyTest(unittest.TestCase):
    def test_baseline_performance_remains_separate_from_faster_tuned_runs(self):
        row = performance_summary(observation())
        self.assertEqual(row["prefill_tokens_per_second_median"], 120.0)
        self.assertEqual(row["generation_tokens_per_second_median"], 9.0)
        self.assertEqual(row["total_service_seconds"], 100)
        self.assertEqual(row["correct_cases"], 1)

    def test_screening_never_selects_incorrect_invalid_or_nonfinite_measurements(self):
        bad = observation(1, identity="batch-1024", correct=False)
        missing = observation(1, identity="mtp-off", valid=False)
        nonfinite = observation(float("nan"), identity="cache-reference")
        self.assertIsNone(choose_candidate(observation(), [bad, missing, nonfinite], 10))
        self.assertEqual(choose_candidate(observation(), [observation(80, identity="batch-1024")], 10), "batch-1024")

    def test_confirmation_requires_both_reversed_blocks_and_every_case(self):
        reference = observation()
        candidate = observation(80, identity="batch-1024")
        self.assertEqual(confirm_candidate([reference, candidate, candidate, reference], 10, 20)["selection"], "batch-1024")
        self.assertEqual(confirm_candidate([reference, candidate, observation(99, identity="batch-1024"), reference], 10, 20)["selection"], "baseline")
        self.assertEqual(confirm_candidate([reference, candidate], 10, 20)["selection"], "unresolved")
        wrong = copy.deepcopy(candidate)
        wrong["cases"][0]["id"] = "different-task"
        self.assertEqual(confirm_candidate([reference, wrong, candidate, reference], 10, 20)["selection"], "unresolved")
        boundary = observation(90, identity="batch-1024")
        self.assertEqual(confirm_candidate([reference, boundary, boundary, reference], 10, 20)["selection"], "batch-1024")

    def test_capacity_and_useful_latency_are_distinct_and_unknown_is_not_failure(self):
        points = [
            {"cache": "q8", "window": 32768, "input_tokens": 27648, "valid": True, "correct": True, "initial_seconds": 240, "followup_seconds": 40},
            {"cache": "q8", "window": 65536, "input_tokens": 60416, "valid": True, "correct": True, "initial_seconds": 900, "followup_seconds": 40},
            {"cache": "q4", "window": 65536, "input_tokens": 60416, "valid": False, "correct": False, "initial_seconds": None, "followup_seconds": None},
        ]
        result = context_summary(points, 300, 60)
        self.assertEqual(result["q8"]["highest_witnessed_input_tokens"], 60416)
        self.assertEqual(result["q8"]["within_budget_window_tokens"], 32768)
        self.assertIsNone(result["q4"]["highest_witnessed_input_tokens"])
        self.assertEqual(result["q4"]["unknown_points"], [65536])

    def test_confirmation_rejects_invalid_evidence_and_duplicate_cases(self):
        reference = observation()
        candidate = observation(80, identity="batch-1024")
        for bad in (None, observation(1, identity="batch-1024", valid=False),
                    observation(1, identity="batch-1024", correct=False),
                    observation(float("nan"), identity="batch-1024")):
            self.assertEqual(confirm_candidate([reference, bad, candidate, reference], 10, 20)["selection"], "unresolved")
        for run in (reference, candidate):
            run["cases"] *= 2
        self.assertEqual(confirm_candidate([reference, candidate, candidate, reference], 10, 20)["selection"], "unresolved")

    def test_total_gain_cannot_hide_a_regression_in_one_case(self):
        reference = observation()
        reference["cases"].append({"id": "rewind", "correct": True, "service_seconds": 10})
        candidate = copy.deepcopy(reference)
        candidate["configuration"] = "batch-1024"
        candidate["cases"][0]["service_seconds"] = 10
        candidate["cases"][1]["service_seconds"] = 15
        self.assertEqual(confirm_candidate([reference, candidate, candidate, reference], 10, 20)["selection"], "baseline")

    def test_variants_preserve_supply_and_the_baseline(self):
        lock = {"records": {"layouts": {"qwen": {"context_window_tokens": 32768,
                    "engine_config": {"batch_tokens": 512, "microbatch_tokens": 512, "kv_cache": "q8", "context_checkpoints": 14, "prompt_cache_ram_mib": 2048},
                    "speculation": {"method": "mtp", "source": "embedded", "max_draft_tokens": 3}}},
                    "artifacts": {"weights": {"sha256": "unchanged"}}}}
        original = copy.deepcopy(lock)
        changed = variant_records(lock, "qwen", "batch-1024")
        self.assertEqual(changed["layouts"]["qwen"]["engine_config"]["batch_tokens"], 1024)
        self.assertEqual(changed["artifacts"], lock["records"]["artifacts"])
        self.assertEqual(lock, original)
        with self.assertRaises(ValueError): variant_records(lock, "qwen", "arbitrary-flags")
