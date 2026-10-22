"""The attempt queue runs planned attempts in order and stops where a person must look.

Failure meanings: running past a failed, unsafe or resource-stopped attempt
would spend the machine on results nobody reviewed; starting an invalid plan
would leave half a queue behind.
"""
from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime import attempt, attempt_queue, workloads
from fieldkit_runtime.approval import preview
from fieldkit_runtime.common import Refusal, canonical_json, load_json
from fieldkit_runtime.study import Study
from tests.test_research import FakeProbe, FakeTemper, fake_context, lock

def answer(probe, path, payload, timeout, **kwargs):
    if kwargs.get("method") == "GET":
        return {}
    return {"message": {"content": "FIELD_KIT_OK", "reasoning": "", "tool_calls": []}, "finish_reason": "stop",
            "stream_complete": True, "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

class QueueTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        FakeTemper.reset()
        source = self.root / "candidate.lock.json"
        source.write_bytes(canonical_json(lock()))
        brief = self.root / "brief.md"
        brief.write_text("Run the planned screen without agent turns between attempts.")
        (self.root / "smoke.json").write_bytes(canonical_json(workloads.generate("smoke")))
        cache = self.root / "hf-cache"
        cache.mkdir()
        self.study = Study(self.root, "queued", fake_context(answer))
        self.study.initialize(preview(FakeTemper(), [source], brief, hours=1, download_gib=1,
                                      weights_cache=cache, context=self.study.context), FakeTemper())

    def plan(self, *attempts):
        path = self.root / "plan.json"
        path.write_bytes(canonical_json({"attempts": [
            {"candidate": "research-model", "workload": "smoke.json", "reason": f"screen {number}", **extra}
            for number, extra in enumerate(attempts, 1)]}))
        return path

    def run_queue(self, plan, context=None):
        self.study.context = context or fake_context(answer)
        return attempt_queue.run(self.study, plan, attempt.execute)

    def test_attempts_run_in_order_and_the_record_names_each(self):
        result = self.run_queue(self.plan({}, {}))
        self.assertEqual(([row["state"] for row in result["attempts"]], result["stopped"], result["not_started"]),
                         (["complete", "complete"], None, 0))
        self.assertEqual([row["run"] for row in result["attempts"]], self.study.load()["runs"])
        self.assertEqual(load_json(Path(result["record"]))["attempts"], result["attempts"])

    def test_a_stop_file_ends_the_queue_before_the_next_attempt(self):
        (self.study.directory / "STOP").touch()
        result = self.run_queue(self.plan({}, {}))
        self.assertEqual((result["attempts"], result["not_started"]), ([], 2))
        self.assertIn("STOP file", result["stopped"])

    def test_an_attempt_that_needs_review_stops_the_queue(self):
        def broken(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            raise OSError("connection reset by the engine")
        result = self.run_queue(self.plan({}, {}), fake_context(broken))
        self.assertEqual(([row["state"] for row in result["attempts"]], result["not_started"]), (["failed"], 1))
        self.assertIn("attempt 1 needs review: attempt ended failed", result["stopped"])

    def test_a_resource_stop_stops_the_queue(self):
        class Stopped(FakeProbe):
            def finish(self):
                return {**super().finish(), "stop_reasons": ["swap growth exceeded 512 MiB"]}
        context = fake_context(answer)
        context = type(context)(**{**context.__dict__, "probe": Stopped})
        result = self.run_queue(self.plan({}, {}), context)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertIn("ended failed (resource-stop", result["stopped"])

    def test_an_unproven_shutdown_stops_the_queue(self):
        class Lingering(FakeProbe):
            def finish(self):
                return {**super().finish(), "safe_to_cleanup": False}
        context = fake_context(answer)
        context = type(context)(**{**context.__dict__, "probe": Lingering})
        result = self.run_queue(self.plan({}, {}), context)
        self.assertEqual((len(result["attempts"]), result["attempts"][0]["state"]), (1, "retained"))
        self.assertIn("ended retained", result["stopped"])

    def test_an_invalid_plan_starts_nothing(self):
        for change, message in (({"candidate": "another-model"}, "outside approved material"),
                                ({"workload": "missing.json"}, "not found"),
                                ({"reason": " "}, "name the question"),
                                ({"temperature": 1}, "optional")):
            with self.subTest(message=message), self.assertRaisesRegex(Refusal, message):
                self.run_queue(self.plan({}, change))
        self.assertEqual(self.study.load()["runs"], [])

    def test_a_refused_attempt_ends_the_queue_with_the_reason(self):
        state = self.study.load()
        state["deadline"] = 0
        self.study.save(state)
        result = self.run_queue(self.plan({}))
        self.assertIn("approved time ended", result["stopped"])

class ReplayTest(unittest.TestCase):
    def test_a_recorded_request_becomes_a_forced_length_timing_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            recorded = Path(temp) / "request.json"
            recorded.write_bytes(canonical_json({"model": "preset", "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "Repair it"}], "tools": [{"type": "function", "function": {"name": "submit"}}],
                "seed": 421, "max_tokens": 900, "cache_prompt": True}))
            document = workloads.replay(recorded, 4096)
            request = workloads.validate(document)["sessions"][0]["steps"][0]["request"]
            self.assertEqual((request["max_tokens"], request["cache_prompt"], request["ignore_eos"], request["timings_per_token"]),
                             (4096, False, True, True))
            self.assertEqual((request["seed"], request["tools"][0]["function"]["name"]), (421, "submit"))
            self.assertFalse({"model", "stream", "stream_options"} & set(request))
            self.assertEqual(len(document["replay_of"]["sha256"]), 64)
            recorded.write_bytes(canonical_json({"query": "not a chat request", "documents": ["x"]}))
            with self.assertRaisesRegex(Refusal, "with messages"):
                workloads.replay(recorded)

if __name__ == "__main__":
    unittest.main()
