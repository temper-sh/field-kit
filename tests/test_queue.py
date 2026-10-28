"""The attempt queue runs planned attempts in order and stops where a person must look.

Failure meanings: running past a failed, unsafe or resource-stopped attempt
would spend the machine on results nobody reviewed; starting an invalid plan
would leave half a queue behind. A resume that repeated a complete attempt
would spend a night twice; one that read the edited plan instead of the frozen
copies would break a matched comparison; one that ran beside a live queue or an
unrecovered attempt would duplicate inference. A continuing queue that ran past
a stop request, a resource stop or a machine-wide failure would fill the night
with attempts that say nothing about the presets.
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

    def run_queue(self, plan, context=None, **options):
        self.study.context = context or fake_context(answer)
        return attempt_queue.run(self.study, plan, attempt.execute, **options)

    def resume(self, name, retry=(), context=None, **options):
        self.study.context = context or fake_context(answer)
        return attempt_queue.resume(self.study, name, attempt.execute, retry, **options)

    def crash(self, plan, execute):
        """The queue process dies inside `execute`; returns the queue's name."""
        self.study.context = fake_context(answer)
        with self.assertRaises(KeyboardInterrupt):
            attempt_queue.run(self.study, plan, execute)
        return next((self.study.directory / "queues").iterdir()).name

    @staticmethod
    def entries(result):
        return [(row["number"], row["state"]) for row in result["attempts"]]

    def test_attempts_run_in_order_and_the_record_names_each(self):
        result = self.run_queue(self.plan({}, {}))
        self.assertEqual(([row["state"] for row in result["attempts"]], result["stopped"], result["not_started"]),
                         (["complete", "complete"], None, 0))
        self.assertEqual([row["run"] for row in result["attempts"]], self.study.load()["runs"])
        self.assertEqual(load_json(Path(result["record"]))["attempts"], result["attempts"])

    def test_inputs_are_frozen_before_the_first_attempt(self):
        original = (self.root / "smoke.json").read_bytes()
        def edit_between(study, directory):
            attempt.execute(study, directory)
            changed = workloads.generate("smoke")
            changed["request"]["messages"][0]["content"] = "A different prompt"
            (self.root / "smoke.json").write_bytes(canonical_json(changed))
        self.study.context = fake_context(answer)
        result = attempt_queue.run(self.study, self.plan({}, {}), edit_between)
        self.assertEqual([row["state"] for row in result["attempts"]], ["complete", "complete"])
        sent = [load_json(self.study.directory / "runs" / row["run"] / "workload.json") for row in result["attempts"]]
        self.assertEqual(sent[0], sent[1])
        self.assertNotIn("A different prompt", canonical_json(sent[1]).decode())
        self.assertNotEqual((self.root / "smoke.json").read_bytes(), original)
        self.assertEqual(len(result["frozen_inputs"]), 2)

    def test_a_workload_or_variant_that_cannot_run_is_refused_before_anything_runs(self):
        (self.root / "rerank.json").write_bytes(canonical_json({"question": "Rank", "steps": [
            {"id": "one", "request": {"query": "q", "documents": ["a"]}}]}))
        changed = lock()
        changed["records"]["engines"]["engine"]["release"] = "another"
        (self.root / "other.lock.json").write_bytes(canonical_json(changed))
        for extra, message in (({"workload": "rerank.json"}, "chat-completions"),
                               ({"lock": "other.lock.json"}, "new study plan")):
            with self.subTest(message=message), self.assertRaisesRegex(Refusal, "queue attempt 2: .*" + message):
                self.run_queue(self.plan({}, extra))
        self.assertEqual(self.study.load()["runs"], [])
        self.assertFalse(any((self.study.directory / "queues").glob("*/inputs")))

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
        self.assertIn("ended retained (shutdown-unconfirmed", result["stopped"])

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

    def test_a_crash_after_an_attempt_committed_resumes_without_repeating_it(self):
        def killed_before_recording(study, directory):
            attempt.execute(study, directory)
            raise KeyboardInterrupt("queue process killed before it recorded the attempt")
        name = self.crash(self.plan({}, {}), killed_before_recording)
        self.assertEqual(load_json(self.study.directory / "queues" / name / "queue.json")["attempts"], [])
        result = self.resume(name)
        self.assertEqual(self.entries(result), [(1, "complete"), (2, "complete")])
        self.assertEqual(len(self.study.load()["runs"]), 2)
        self.assertEqual((result["stopped"], result["not_started"], len(result["resumed_at"])), (None, 0, 1))
        self.assertNotIn("resume", result)

    def test_an_interrupted_entry_runs_again_as_a_new_attempt_and_the_first_stays(self):
        def stopped(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            raise KeyboardInterrupt("stop requested")
        first = self.run_queue(self.plan({}, {}), fake_context(stopped))
        self.assertEqual((self.entries(first), first["not_started"]), ([(1, "interrupted")], 1))
        name = Path(first["record"]).parent.name
        self.assertTrue(first["resume"].endswith(f"study queue queued --resume {name}"))
        result = self.resume(name)
        self.assertEqual(self.entries(result), [(1, "interrupted"), (1, "complete"), (2, "complete")])
        runs = self.study.load()["runs"]
        self.assertEqual(len(set(runs)), 3)
        self.assertEqual(load_json(self.study.directory / "runs" / runs[0] / "result.json")["state"], "interrupted")

    def test_an_attempt_lost_with_its_process_is_recovered_before_the_queue_resumes(self):
        def power_lost(study, directory):
            raise KeyboardInterrupt("machine lost power before the attempt began")
        name = self.crash(self.plan({}, {}), power_lost)
        with self.assertRaisesRegex(Refusal, "needs recovery"):
            self.resume(name)
        files = self.study.files(self.study.load()["runs"][0])
        run = files.load_run()
        files.save_run({**run, "created_epoch": run["created_epoch"] - 60})  # past the worker startup grace
        self.study.recover()
        self.assertEqual(self.entries(self.resume(name)), [(1, "interrupted"), (1, "complete"), (2, "complete")])

    def test_a_failed_entry_stands_unless_the_resume_names_it_for_retry(self):
        def broken(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            raise OSError("connection reset by the engine")
        name = Path(self.run_queue(self.plan({}, {}), fake_context(broken))["record"]).parent.name
        self.assertEqual(self.entries(self.resume(name)), [(1, "failed"), (2, "complete")])
        for retry, message in (([], "every entry has a finished attempt"), ([2], "entry 2 completed"),
                               ([3], "entry 3 does not exist")):
            with self.subTest(retry=retry), self.assertRaisesRegex(Refusal, message):
                self.resume(name, retry)
        result = self.resume(name, [1])
        self.assertEqual(self.entries(result), [(1, "failed"), (2, "complete"), (1, "complete")])
        self.assertEqual(len(result["resumed_at"]), 2)

    def test_a_resume_sends_the_frozen_inputs_and_refuses_a_changed_copy(self):
        (self.study.directory / "STOP").touch()
        first = self.run_queue(self.plan({}, {}))
        (self.study.directory / "STOP").unlink()
        name = Path(first["record"]).parent.name
        changed = workloads.generate("smoke")
        changed["request"]["messages"][0]["content"] = "A different prompt"
        (self.root / "smoke.json").write_bytes(canonical_json(changed))
        (self.root / "plan.json").unlink()
        copy = self.study.directory / "queues" / name / "inputs" / "002-workload.json"
        original = copy.read_bytes()
        copy.write_bytes(original + b" ")
        with self.assertRaisesRegex(Refusal, "frozen queue input changed"):
            self.resume(name)
        copy.write_bytes(original)
        result = self.resume(name)
        self.assertEqual(self.entries(result), [(1, "complete"), (2, "complete")])
        for row in result["attempts"]:
            sent = (self.study.directory / "runs" / row["run"] / "workload.json").read_bytes()
            self.assertNotIn(b"A different prompt", sent)

    def test_a_running_queue_cannot_be_resumed_beside_itself(self):
        def resume_beside(study, directory):
            name = next((study.directory / "queues").iterdir()).name
            with self.assertRaisesRegex(Refusal, "still running"):
                attempt_queue.resume(study, name, attempt.execute)
            attempt.execute(study, directory)
        self.study.context = fake_context(answer)
        result = attempt_queue.run(self.study, self.plan({}), resume_beside)
        self.assertEqual(self.entries(result), [(1, "complete")])

    def test_a_resume_names_a_queue_whose_attempts_record_their_entries(self):
        result = self.run_queue(self.plan({}))
        record = load_json(Path(result["record"]))
        del record["entries"]
        Path(result["record"]).write_bytes(canonical_json(record))
        for name, message in (("../queued", "name a queue folder"), ("20990101T000000000000Z", "no queue record"),
                              (Path(result["record"]).parent.name, "predates resume")):
            with self.subTest(name=name), self.assertRaisesRegex(Refusal, message):
                self.resume(name)

    def test_a_continuing_queue_runs_past_failures_of_single_attempts(self):
        failures = [OSError("connection reset by the engine"), OSError("connection reset by the engine")]
        def two_failures(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") != "GET" and failures:
                raise failures.pop()
            return answer(probe, path, payload, timeout, **kwargs)
        context = fake_context(two_failures)
        first = self.run_queue(self.plan({}, {}, {}), context)
        self.assertEqual((self.entries(first), first["continue_after_failure"]), ([(1, "failed")], False))
        result = self.resume(Path(first["record"]).parent.name, context=context, continue_after_failure=True)
        self.assertEqual(self.entries(result), [(1, "failed"), (2, "failed"), (3, "complete")])
        self.assertEqual((result["stopped"], result["continue_after_failure"]), (None, True))
        self.assertNotIn("resume", result)

    def test_a_continuing_queue_still_stops_where_a_person_or_the_machine_needs_it(self):
        def stopped(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            raise KeyboardInterrupt("stop requested")
        class Pressure(FakeProbe):
            def finish(self):
                return {**super().finish(), "stop_reasons": ["swap growth exceeded 512 MiB"]}
        class Lingering(FakeProbe):
            def finish(self):
                return {**super().finish(), "safe_to_cleanup": False}
        base = fake_context(answer)
        for context, ending in ((fake_context(stopped), "interrupted"),
                                (type(base)(**{**base.__dict__, "probe": Pressure}), "resource-stop"),
                                (type(base)(**{**base.__dict__, "probe": Lingering}), "shutdown-unconfirmed")):
            with self.subTest(ending=ending):
                result = self.run_queue(self.plan({}, {}), context, continue_after_failure=True)
                self.assertEqual(len(result["attempts"]), 1)
                self.assertIn(ending, result["stopped"])

    def test_failures_on_two_candidates_in_a_row_stop_a_continuing_queue(self):
        def broken(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            raise OSError("connection reset by the engine")
        sources = []
        for name in ("research-model", "other-model"):
            sources.append(self.root / f"{name}.lock.json")
            sources[-1].write_bytes(canonical_json(lock(name)))
        study = Study(self.root, "paired", fake_context(broken))
        study.initialize(preview(FakeTemper(), sources, self.root / "brief.md", hours=1, download_gib=1,
                                 weights_cache=self.root / "hf-cache", context=study.context), FakeTemper())
        plan = self.root / "paired.json"
        plan.write_bytes(canonical_json({"attempts": [{"candidate": name, "workload": "smoke.json", "reason": "screen"}
            for name in ("research-model", "research-model", "other-model", "other-model")]}))
        result = attempt_queue.run(study, plan, attempt.execute, continue_after_failure=True)
        self.assertEqual((self.entries(result), result["not_started"]), ([(1, "failed"), (2, "failed"), (3, "failed")], 1))
        self.assertIn("attempt 3 needs review", result["stopped"])
        self.assertIn("attempt 2 on research-model failed just before", result["stopped"])

class StopRuleTest(unittest.TestCase):
    """The continuing rule alone: a failure of one preset goes on; a pattern across presets stops."""
    @staticmethod
    def attempt(number, candidate, state="failed", kind="runtime-failure"):
        return {"number": number, "candidate": candidate, "state": state,
                "error": None if state == "complete" else {"kind": kind, "message": "model HTTP status 500"}}

    def test_a_failure_right_after_one_on_another_candidate_stops_a_continuing_queue(self):
        a1, a2, b3 = self.attempt(1, "a"), self.attempt(2, "a"), self.attempt(3, "b")
        self.assertIsNone(attempt_queue.stop_reason(a2, a1, True))
        self.assertIsNone(attempt_queue.stop_reason(b3, self.attempt(2, "a", "complete"), True))
        self.assertIn("attempt 2 on a failed just before", attempt_queue.stop_reason(b3, a2, True))
        for kind in ("storage", "stopped-by-watch", "resource-stop"):
            with self.subTest(kind=kind):
                self.assertIn(kind, attempt_queue.stop_reason(self.attempt(1, "a", kind=kind), None, True))
        self.assertIn("retained", attempt_queue.stop_reason(self.attempt(1, "a", "retained"), None, True))
        self.assertIn("runtime-failure", attempt_queue.stop_reason(a1, None, False))

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
