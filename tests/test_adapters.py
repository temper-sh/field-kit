"""Adapters preserve evidence and select behavior without model-name heuristics."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime.adapters import engines, models
from fieldkit_runtime.common import Refusal
from fieldkit_runtime.measurements import POLICY, policy_for, watch_spec
from fieldkit_runtime.stream import ChatStream
from fieldkit_runtime.workloads import correct
from tests.test_research import FACTS


TOOL = {"type": "function", "function": {"name": "test_patch", "parameters": {
    "type": "object", "properties": {"explanation": {"type": "string"}, "edits": {"type": "array"},
                                      "count": {"type": "integer"}, "apply": {"type": "boolean"}}}}}
REQUEST = {"messages": [{"role": "user", "content": "Repair sample.py"}], "tools": [TOOL]}


def reply(content="", *, reasoning="", calls=None, complete=True):
    return {"message": {"role": "assistant", "content": content, "reasoning": reasoning, "tool_calls": calls or []},
            "stream_complete": complete, "finish_reason": "stop" if complete else None, "first_answer_seconds": 1}


class EngineAdapterTest(unittest.TestCase):
    def test_native_rate_owns_its_timed_interval_and_cache_is_separate(self):
        # Retained llama.cpp response: two tokens, but only one timed decode
        # interval. Recomputing predicted_n/predicted_ms doubles the native rate.
        response = {"usage": {"prompt_tokens": 1020, "completion_tokens": 2,
                               "prompt_tokens_details": {"cached_tokens": 1000}},
                    "timings": {"prompt_n": 20, "prompt_ms": 174.911, "prompt_per_second": 114.3439,
                                "predicted_n": 2, "predicted_ms": 22.444, "predicted_per_second": 44.5553}}
        for identifier in ("llama-server/v1", "splash/v1", "splash/v2"):
            with self.subTest(adapter=identifier):
                result = engines.resolve(identifier).metrics(response)
                self.assertEqual(result["generation_tokens_per_second"], 44.5553)
                self.assertEqual(result["prefill_tokens_per_second"], 114.3439)
                self.assertEqual((result["input_tokens"], result["cached_tokens"]), (1020, 1000))
                self.assertEqual(result["rate_sources"]["generation"], "timings.predicted_per_second")

    def test_missing_and_invalid_metrics_are_unmeasured_not_wall_time_estimates(self):
        for identifier in engines.ADAPTERS:
            with self.subTest(adapter=identifier):
                result = engines.resolve(identifier).metrics({"usage": {"completion_tokens": 0}, "service_seconds": .01})
                self.assertEqual(result["output_tokens"], 0)
                self.assertIsNone(result["input_tokens"])
                self.assertIsNone(result["prefill_tokens_per_second"])
                self.assertIsNone(result["generation_tokens_per_second"])
        adapter = engines.resolve("llama-server/v1")
        for invalid in (True, -1, "123", [], float("nan"), float("inf"), 10**400):
            with self.subTest(invalid=str(invalid)[:25]):
                result = adapter.metrics({"usage": {"prompt_tokens": invalid}, "timings": {
                    "predicted_per_second": invalid, "predicted_n": 20, "predicted_ms": 200}})
                self.assertIsNone(result["generation_tokens_per_second"])
        result = adapter.metrics({"usage": [], "timings": {"prompt_n": 0, "prompt_ms": 5,
                                                                          "predicted_n": 20, "predicted_ms": 200}})
        self.assertEqual(result["generation_tokens_per_second"], 100)
        self.assertEqual(result["prefill_tokens_per_second"], 0)
        self.assertIn(" / ", result["rate_sources"]["generation"])

    def test_role_limits_are_disclosed_and_old_approvals_do_not_expand(self):
        adapter = engines.resolve("vllm-metal/v1")
        new = policy_for([adapter])
        self.assertEqual(new["process_watch"]["helper_limits_by_adapter"][adapter.identifier]["frontend"], 3 * 1024**3)
        for policy, frontend_gib in ((new, 3), (POLICY, 2)):
            roles = {role["id"]: role for role in watch_spec(policy, FACTS, adapter)["roles"]}
            self.assertEqual(set(roles), {"engine", "router", "frontend", "resource-tracker"})
            self.assertEqual(roles["frontend"]["rss_bytes_max"], frontend_gib * 1024**3)
            self.assertEqual(roles["router"]["rss_bytes_max"], 2 * 1024**3)
        self.assertNotIn("helper_limits_by_adapter", POLICY["process_watch"])

    def test_unknown_versions_are_not_inferred_from_a_family_name(self):
        for identifier in ("llama-server/v999", "splash", None, []):
            with self.assertRaisesRegex(Refusal, "engine adapter"):
                engines.resolve(identifier)
        with self.assertRaisesRegex(Refusal, "model adapter"):
            models.resolve("flint-preview-q4")


class ModelAdapterTest(unittest.TestCase):
    def setUp(self):
        self.flint = models.resolve("flint/v1")

    def test_nested_multiline_json_and_literal_tags_survive_stream_boundaries(self):
        edits = [{"path": "sample.py", "old_text": 'return "old"\n',
                  "new_text": 'return "</arg_value><tool_call> λ"\n'}]
        raw = ('<think>Preserved thought.</think><tool_call>test_patch'
               '<arg_key>explanation</arg_key><arg_value>001 true []</arg_value>'
               '<arg_key>edits</arg_key><arg_value>' + json.dumps(edits, ensure_ascii=False) + '</arg_value>'
               '<arg_key>count</arg_key><arg_value>2</arg_value>'
               '<arg_key>apply</arg_key><arg_value>false</arg_value></tool_call>')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "response.sse"
            # One character per delta splits every marker and escaped JSON value.
            events = [{"choices": [{"delta": {"content": character}}]} for character in raw]
            events.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            wire = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"
            captured = ChatStream(path, clock=lambda: 2)(io.BytesIO(wire), 1)
            result = self.flint.normalize(captured, REQUEST)
            self.assertEqual(path.read_bytes().rstrip(b"\n"), wire.rstrip(b"\n"))
        self.assertEqual(result["message"]["reasoning"], "Preserved thought.")
        self.assertEqual(result["message"]["content"], "")
        call = result["message"]["tool_calls"][0]
        self.assertEqual(json.loads(call["function"]["arguments"]),
                         {"explanation": "001 true []", "edits": edits, "count": 2, "apply": False})
        self.assertEqual(result["wire_message"]["content"], raw)
        self.assertEqual(result["model_format"]["tool_call_ids"], "assigned-by-field-kit")
        self.assertIsNone(result["first_answer_seconds"])
        self.assertEqual(captured["message"]["content"], raw)
        next_request = {**REQUEST, "messages": REQUEST["messages"] + [self.flint.assistant_message(result)]}
        later = self.flint.normalize(captured, next_request)
        self.assertNotEqual(call["id"], later["message"]["tool_calls"][0]["id"])

    def test_partial_or_invalid_tagged_output_is_evidence_not_an_actionable_call(self):
        raw = '<tool_call>test_patch<arg_key>edits</arg_key><arg_value>[]</arg_value></tool_call>'
        for end in range(len("<tool_call>"), len(raw) + 1):
            partial = reply(raw[:end], complete=False)
            result = self.flint.normalize(partial, REQUEST)
            self.assertEqual(result["message"], partial["message"])
            self.assertEqual(result["model_format"]["state"], "partial")
            with self.assertRaisesRegex(Refusal, "replay"):
                self.flint.assistant_message(result)
        for raw in (raw[:-1], '<tool_call>missing</tool_call>',
                    '<tool_call>test_patch<arg_key>count</arg_key><arg_value>true</arg_value></tool_call>',
                    '<tool_call>test_patch<arg_key>edits</arg_key><arg_value>[NaN]</arg_value></tool_call>'):
            result = self.flint.normalize(reply(raw), REQUEST)
            self.assertEqual(result["model_format"]["state"], "invalid")
            self.assertEqual(result["message"]["content"], raw)
            self.assertIsNone(correct(result, {"tool": {"name": "test_patch", "arguments": {}}}))

    def test_native_calls_and_prose_are_not_reparsed_and_reasoning_replays(self):
        call = {"id": "original-call", "type": "function", "function": {
            "name": "test_patch", "arguments": '{"explanation":"<tool_call>literal</tool_call>"}'}}
        native = reply(calls=[call], reasoning="Reason before the rejected call.")
        result = self.flint.normalize(native, REQUEST)
        self.assertEqual(result["message"], native["message"])
        replay = self.flint.assistant_message(result)
        self.assertEqual(replay["reasoning_content"], native["message"]["reasoning"])
        self.assertEqual(replay["tool_calls"], [call])
        replay["tool_calls"][0]["id"] = "changed copy"
        self.assertEqual(native["message"]["tool_calls"][0]["id"], "original-call")
        for text in ('Explain <tool_call> markup.', '```xml\n<tool_call>example\n```'):
            self.assertEqual(self.flint.normalize(reply(text), REQUEST)["message"]["content"], text)
        plain = self.flint.normalize(reply("Answer", reasoning="Thought without a tool"), REQUEST)
        self.assertEqual(self.flint.assistant_message(plain)["reasoning_content"], "Thought without a tool")

    def test_default_adapter_does_not_guess_flint_and_ambiguous_calls_cannot_replay(self):
        adapter = models.resolve()
        raw = reply("<think>literal</think>")
        self.assertEqual(adapter.normalize(raw, REQUEST)["message"], raw["message"])
        call = {"id": "same", "type": "function", "function": {"name": "test_patch", "arguments": "{}"}}
        for calls in ([call, copy.deepcopy(call)], [{**call, "id": ""}], [None]):
            with self.assertRaisesRegex(Refusal, "ambiguous"):
                self.flint.assistant_message(reply(calls=calls))
