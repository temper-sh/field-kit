"""rerank/v1: reranker presets are measured like chat presets; their suite grades them."""
from pathlib import Path
import sys
import tempfile
import unittest

from fieldkit_runtime import attempt, workloads
from fieldkit_runtime.adapters import interfaces
from fieldkit_runtime.adapters.interfaces import rerank
from fieldkit_runtime.approval import preview
from fieldkit_runtime.common import Refusal, canonical_json, load_json
from fieldkit_runtime.study import Study
from tests.test_research import FakeTemper, fake_context, lock

QUERY = {"query": "How is a timeout told apart from cancellation?", "documents": ["Timeouts raise.", "Cancel is a signal.", "Unrelated."]}

def reranker_lock(name="ettin-reranker"):
    document = lock(name)
    document["records"]["engines"]["engine"]["adapter"] = "crispembed/v1"
    selected = document["records"]["presets"][name]
    selected.update(interface="reranking", request_defaults={})
    del selected["template"]
    del document["records"]["templates"]
    return document

def answer(scores, usage=None):
    response = {"results": [{"index": index, "relevance_score": score} for index, score in scores]}
    return {**response, "usage": usage} if usage else response

class RerankTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        FakeTemper.reset()
        self.source = self.root / "ettin.lock.json"
        self.source.write_bytes(canonical_json(reranker_lock()))
        self.brief = self.root / "brief.md"
        self.brief.write_text("Do the reranker presets order the fixed cases correctly, how fast, at what memory?")
        self.cache = self.root / "hf-cache"
        self.cache.mkdir()
        self.workload = self.root / "rerank.json"
        self.write_workload({"question": "Rank the fixed cases", "sessions": [{"id": "suite", "steps": [
            {"id": "first-forward-1", "request": QUERY},
            {"id": "first-reverse-1", "request": {**QUERY, "documents": list(reversed(QUERY["documents"]))}}]}]})
        self.study = Study(self.root, "rerankers", fake_context())
        self.study.initialize(preview(FakeTemper(), [self.source], self.brief, hours=1, download_gib=1,
                                      weights_cache=self.cache, context=self.study.context), FakeTemper())

    def write_workload(self, document):
        self.workload.write_bytes(canonical_json(document))

    def run_with(self, responses):
        calls = []
        def call(probe, path, payload, timeout, **kwargs):
            calls.append((path, payload.get("model"), payload.get("top_n")))
            if kwargs.get("method") == "GET":
                return {"status": "ok"}
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        directory = self.study.reserve("ettin-reranker", self.workload, "Measure the fixed reranking cases")
        self.study.context = fake_context(call)
        attempt.execute(self.study, directory)
        return directory, load_json(directory / "result.json", 64 * 1024**2), calls

    def test_scores_rates_and_memory_are_recorded_in_document_order(self):
        directory, result, calls = self.run_with([
            answer([(1, 0.2), (0, 0.9), (2, -1.5)], {"prompt_tokens": 60, "total_tokens": 60}),
            answer([(0, -1.4), (1, 0.3), (2, 0.8)])])
        self.assertEqual(result["state"], "complete")
        self.assertEqual((result["engine_adapter"], result["model_adapter"], result["model_adapter_scope"]),
                         ("crispembed/v1", None, None))
        self.assertEqual(calls, [("/upstream/ettin-reranker/health/ready", None, None),
                                 ("/upstream/ettin-reranker/rerank", "ettin-reranker", 3),
                                 ("/upstream/ettin-reranker/rerank", "ettin-reranker", 3)])
        first, second = result["sessions"][0]["steps"]
        self.assertEqual(first["scores"], [0.9, 0.2, -1.5])
        self.assertEqual(second["scores"], [-1.4, 0.3, 0.8])
        self.assertEqual((first["documents"], first["input_tokens"]), (3, 60))
        self.assertGreater(first["documents_per_second"], 0)
        self.assertIsNone(second["input_tokens_per_second"])
        self.assertTrue(first["response_complete"])
        self.assertIsNotNone(result["sessions"][0]["memory"]["engine_peak_wired_bytes"])
        step = directory / "sessions/suite/first-forward-1"
        self.assertEqual(load_json(step / "request.json")["documents"], QUERY["documents"])
        self.assertEqual(load_json(step / "response.json")["usage"]["prompt_tokens"], 60)

    def test_an_incomplete_ranking_is_a_failure_that_stops_the_session(self):
        for bad, message in ((answer([(0, 0.9), (1, 0.2)]), "2 results for 3"),
                             (answer([(0, 0.9), (0, 0.2), (2, 0.1)]), "duplicate index"),
                             (answer([(0, 0.9), (1, float("nan")), (2, 0.1)]), "non-finite")):
            with self.subTest(message=message):
                directory, result, calls = self.run_with([bad, answer([(0, 1), (1, 2), (2, 3)])])
                self.assertEqual(result["state"], "failed")
                steps = result["sessions"][0]["steps"]
                self.assertEqual(len(steps), 1)
                self.assertIn(message, steps[0]["failure"]["message"])
                self.assertIsNone(steps[0]["scores"])
                self.assertEqual(len(calls), 2)

    def test_an_over_context_rejection_keeps_its_kind_and_no_scores(self):
        from fieldkit_runtime.serving import HTTPResponseError
        directory, result, _ = self.run_with([HTTPResponseError("model HTTP status 400: b'input exceeds the context size'")])
        step = result["sessions"][0]["steps"][0]
        self.assertEqual(step["failure"]["kind"], "exceeds-context")
        self.assertIsNone(step["scores"])
        self.assertFalse((directory / "sessions/suite/first-forward-1/response.json").exists())

    def test_workloads_refuse_chat_only_fields(self):
        interface = interfaces.resolve("reranking")
        base = {"question": "Rank", "steps": [{"id": "one", "request": QUERY}]}
        self.assertIsNone(workloads.validate(base, interface)["model_adapter"])
        for change, message in (({"model_adapter": "flint/v1"}, "no model format"),
                                ({"steps": [{"id": "one", "request": QUERY, "expect": {"text": "x"}}]}, "take no expect"),
                                ({"steps": [{"id": "one", "request": QUERY}, {"id": "two", "request": QUERY, "append_previous": True}]}, "chat history"),
                                ({"steps": [{"id": "one", "request": {**QUERY, "top_n": 1}}]}, "exactly a query"),
                                ({"steps": [{"id": "one", "request": {**QUERY, "documents": []}}]}, "nonempty list"),
                                ({"steps": [{"id": "one", "request": {**QUERY, "query": " "}}]}, "nonempty string")):
            with self.subTest(message=message), self.assertRaisesRegex(Refusal, message):
                workloads.validate({**base, **change}, interface)
        with self.assertRaisesRegex(Refusal, "exactly a query"):
            workloads.validate({"question": "Chat", "request": {"messages": [{"role": "user", "content": "hi"}]}}, interface)

    def test_a_chat_workload_is_refused_for_a_reranker_before_any_effect(self):
        self.write_workload(workloads.generate("smoke"))
        with self.assertRaises(Refusal):
            self.study.reserve("ettin-reranker", self.workload, "Wrong interface")
        self.assertEqual(self.study.load()["runs"], [])

    def test_a_harness_driving_a_reranker_gets_no_model_adapter(self):
        script = self.root / "harness.py"
        script.write_text("import os, pathlib\np = pathlib.Path(os.environ['FIELD_KIT_OUTPUT_DIR'])\n"
                          "(p / 'seen.txt').write_text(os.environ.get('FIELD_KIT_MODEL_ADAPTER', 'none') + ' ' + os.environ['FIELD_KIT_NATIVE_URL'])\n")
        self.write_workload({"question": "Suite command", "command": ["{python}", str(script)], "cwd": "."})
        directory, result, _ = self.run_with([])
        self.assertEqual(result["state"], "complete")
        self.assertRegex((directory / "sessions/task/output/seen.txt").read_text(), r"^none http://127\.0\.0\.1:\d+/upstream/ettin-reranker$")

    def test_scores_accept_the_data_and_score_spelling(self):
        self.assertEqual(rerank.scores({"data": [{"index": 1, "score": 2}, {"index": 0, "score": 1.5}]}, 2), [1.5, 2.0])

if __name__ == "__main__":
    unittest.main()
