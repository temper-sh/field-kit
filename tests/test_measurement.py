import io
import json
import unittest

from fieldkit_runtime.measurement import read_stream, construct_context
from fieldkit_runtime.probe import ProbeError


class StreamingTest(unittest.TestCase):
    def stream(self, events, done=True):
        raw = b": heartbeat\n\n" + b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        return io.BytesIO(raw + (b"data: [DONE]\n\n" if done else b""))

    def test_first_token_is_not_headers_role_or_first_delivered_answer(self):
        events = [
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"reasoning_content": "Think"}}]},
            {"choices": [{"delta": {"content": "{\"ok\":true}"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 42, "completion_tokens": 10}, "timings": {"prompt_n": 42}},
        ]
        ticks = iter((101, 103, 107, 108))
        result = read_stream(self.stream(events), 100, clock=lambda: next(ticks))
        self.assertEqual(result["first_token_seconds"], 3)
        self.assertEqual(result["first_answer_seconds"], 7)
        self.assertEqual(result["service_seconds"], 8)
        self.assertEqual(result["content"], '{"ok":true}')
        self.assertEqual(result["usage"]["prompt_tokens"], 42)

    def test_truncated_stream_never_becomes_a_completed_measurement(self):
        result = read_stream(self.stream([{"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]}], done=False), 0)
        self.assertFalse(result["stream_complete"])
        self.assertEqual(result["content"], "answer")

    def test_context_contains_distributed_source_facts_and_exact_native_length(self):
        def count(messages):
            return messages[0]["content"].count(" x") + 200
        messages, expected, followup = construct_context(27648, count)
        self.assertEqual(count(messages), 27648)
        for key, value in {**expected, **followup}.items():
            self.assertIn(f"{key}={value}", messages[0]["content"])
        self.assertEqual(list(expected), ["R01", "R03", "R05"])
        self.assertEqual(list(followup), ["R02", "R04"])
