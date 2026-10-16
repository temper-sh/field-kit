"""A streamed completion keeps partial reasoning and tool calls."""
import io
import json
from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime.stream import CodingStream


class CodingStreamTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_fragmented_reasoning_and_tool_calls_preserve_partial_answers(self):
        stream=CodingStream(self.root/"response.sse",clock=lambda:4.0)
        events=[{"choices":[{"delta":{"reasoning":"thinking"}}]},
                {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call", "function":{"name":"submit_","arguments":"{\"a\":"}}]}}]},
                {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"patch","arguments":"1}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":4}}]
        wire=b"".join(b"data: "+json.dumps(e).encode()+b"\n\n" for e in events)+b"data: [DONE]\n\n"
        result=stream(io.BytesIO(wire),1.0)
        self.assertEqual(result["message"]["reasoning"],"thinking")
        self.assertEqual(result["message"]["tool_calls"][0]["function"],{"name":"submit_patch","arguments":'{"a":1}'})
        self.assertTrue(result["stream_complete"])
        partial=CodingStream(self.root/"partial.sse",clock=lambda:4.0)
        with self.assertRaises(ValueError): partial(io.BytesIO(wire.split(b"data: [DONE]")[0]+b"data: invalid\n"),1.0)
        self.assertEqual(partial.snapshot()["message"],result["message"])
        self.assertFalse(partial.snapshot()["stream_complete"])
