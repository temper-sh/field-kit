"""The comparison reports what differed and what was measured, and never fills a gap.

Failure meanings: a missed setting change or a wrong decode rate would mislead a
tuning decision; a variant that changes a declared fixed setting would quietly
break a matched comparison.
"""
import json
from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime import attempt, workloads
from fieldkit_runtime.approval import preview
from fieldkit_runtime.common import Refusal, canonical_json
from fieldkit_runtime.compare import compare, stream
from fieldkit_runtime.material import arguments_map
from fieldkit_runtime.study import Study
from tests.test_research import FakeTemper, fake_context, lock

def sse(rates, text="OK", tool_at=None):
    """A stream whose native per-token timings decode at the given tokens/s, 25 tokens per rate."""
    lines, n, ms = [], 0, 0.0
    for rate in rates:
        for _ in range(25):
            n += 1
            ms += 1000 / rate
            delta = {"content": text if n == 1 else ""}
            if tool_at == n:
                delta = {"tool_calls": [{"index": 0, "id": "call", "function": {"name": "submit", "arguments": "{}"}}]}
            lines.append({"choices": [{"index": 0, "delta": delta}],
                          "timings": {"prompt_n": 90, "cache_n": 10, "prompt_ms": 45.0, "predicted_n": n, "predicted_ms": ms}})
    return b"".join(b"data: " + json.dumps(line).encode() + b"\n\n" for line in lines) + b"data: [DONE]\n\n"

class StreamFactsTest(unittest.TestCase):
    def test_decode_quarters_show_a_sustained_slowdown_from_native_timings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "response.sse"
            path.write_bytes(sse([40, 20, 15, 10], tool_at=60))
            facts = stream(path)
            self.assertEqual(facts["decode_quarters"], [40.0, 20.0, 15.0, 10.0])
            self.assertEqual((facts["sustained_ratio"], facts["generated_tokens"]), (0.25, 100))
            self.assertEqual((facts["newly_read_tokens"], facts["cached_tokens"], facts["first_tool_call_at_token"]), (90, 10, 60))
            path.write_bytes(b'data: {"choices":[{"index":0,"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
            facts = stream(path)
            self.assertIn("no per-token timings", facts["decode_quarters"])
            self.assertIsNone(facts["sustained_ratio"])

    def test_a_harness_attempt_from_before_the_relay_is_read_from_its_own_records(self):
        from fieldkit_runtime.compare import generations
        with tempfile.TemporaryDirectory() as temp:
            turn = Path(temp) / "sessions/task/output/turn-001"
            turn.mkdir(parents=True)
            (turn / "response.sse").write_bytes(sse([40, 30, 20, 10]))
            rows = generations(Path(temp), {"id": "task", "command": {"returncode": 0}, "steps": []})
            self.assertEqual([(row["label"], row["source"], row["sustained_ratio"]) for row in rows],
                             [("task/turn-001", "harness record", 0.25)])

    def test_engine_flags_and_negative_values_are_read_as_settings(self):
        self.assertEqual(arguments_map(["-c", "65536", "--predict", "-1", "--jinja", "-t", "4"]),
                         {"-c": "65536", "--predict": "-1", "--jinja": True, "-t": "4"})

class CompareTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        FakeTemper.reset()
        self.base = self.root / "base.lock.json"
        self.base.write_bytes(canonical_json(lock()))
        self.brief = self.root / "brief.md"
        self.brief.write_text("Tune one setting at a time with the context fixed.")
        self.workload = self.root / "smoke.json"
        self.workload.write_bytes(canonical_json(workloads.generate("smoke")))
        self.cache = self.root / "hf-cache"
        self.cache.mkdir()

    def variant(self, name, **engine_config):
        document = lock()
        profile = document["records"]["presets"]["research-model"]["profiles"][0]
        profile["engine_config"].update(engine_config)
        if "window" in engine_config:
            profile["context_window_tokens"] = profile["engine_config"].pop("window")
        path = self.root / name
        path.write_bytes(canonical_json(document))
        return path

    def open_study(self, fixed=()):
        study = Study(self.root, "tuning", fake_context())
        study.initialize(preview(FakeTemper(), [self.base], self.brief, hours=1, download_gib=1, weights_cache=self.cache,
                                 context=study.context, fixed=fixed), FakeTemper())
        return study

    def run_attempt(self, study, rates, variant=None, text="FIELD_KIT_OK"):
        def call(probe, path, payload, timeout, **kwargs):
            if kwargs.get("method") == "GET":
                return {}
            kwargs["stream_reader"].raw_path.write_bytes(sse(rates, text=text))
            return {"message": {"content": "FIELD_KIT_OK", "reasoning": "", "tool_calls": []}, "finish_reason": "stop",
                    "stream_complete": True, "usage": {"prompt_tokens": 100, "completion_tokens": 100}}
        directory = study.reserve("research-model", self.workload, "Compare one setting", variant)
        study.context = fake_context(call)
        attempt.execute(study, directory)
        return directory.name

    def test_settings_rates_outputs_and_memory_side_by_side(self):
        study = self.open_study()
        first = self.run_attempt(study, [40, 40, 40, 40])
        second = self.run_attempt(study, [40, 30, 20, 10], self.variant("q4.lock.json", kv_cache="q4"))
        result = compare(study, [first, second])
        self.assertTrue(result["same_material"])
        self.assertEqual(result["setting_differences"], {"profile.engine_config.kv_cache": ["q8", "q4"]})
        self.assertEqual(result["workload_differences"], {})
        self.assertEqual(result["output_identity"]["same_label"], {"request/request": "identical"})
        rows = [attempt_row["sessions"][0]["generations"][0] for attempt_row in result["attempts"]]
        self.assertEqual([row["sustained_ratio"] for row in rows], [1.0, 0.25])
        memory = result["attempts"][1]["sessions"][0]["memory"]
        self.assertEqual((memory["counter"], memory["engine_peak_wired_bytes"] > 0), ("system-wired-growth", True))
        third = self.run_attempt(study, [40, 40, 40, 40], text="FIELD_KIT_NO")
        changed = compare(study, [first, third])["output_identity"]
        self.assertEqual((changed["same_label"], changed["identical_text"]), ({"request/request": "different"}, []))
        with self.assertRaisesRegex(Refusal, "has not finished"):
            compare(study, ["20000101T000000000000Z"])

    def test_a_declared_fixed_setting_cannot_change_in_a_variant(self):
        with self.assertRaisesRegex(Refusal, "fixed setting engine_config.cache_type is not a setting"):
            preview(FakeTemper(), [self.base], self.brief, hours=1, download_gib=1, weights_cache=self.cache,
                    context=fake_context(), fixed=["engine_config.cache_type"])
        study = self.open_study(fixed=["context_window_tokens"])
        with self.assertRaisesRegex(Refusal, r"fixed setting context_window_tokens: 4096 → 8192"):
            study.reserve("research-model", self.workload, "Bigger window", self.variant("wide.lock.json", window=8192))
        allowed = self.run_attempt(study, [40, 40, 40, 40], self.variant("q4.lock.json", kv_cache="q4"))
        result = compare(study, [allowed])
        self.assertEqual((result["fixed_settings"], result["fixed_settings_changed"]), (["context_window_tokens"], []))
        self.assertEqual(result["attempts"][0]["fixed_settings"], {"context_window_tokens": 4096})

if __name__ == "__main__":
    unittest.main()
