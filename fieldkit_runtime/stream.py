"""Read one streamed chat completion, keeping partial output when it stops early."""
from __future__ import annotations

import json
import time

from .probe import ProbeError


class ChatStream:
    """Keep partial reasoning and tool arguments when a request is interrupted."""
    def __init__(self, raw_path, *, clock=time.monotonic):
        self.raw_path, self.clock = raw_path, clock
        self.started = None
        self.content, self.reasoning, self.calls = [], [], {}
        self.first_token = self.first_answer = None
        self.usage = self.timings = self.finish = None
        self.done = False

    def __call__(self, response, started):
        self.started = started
        received = 0
        with self.raw_path.open("xb") as output:
            while True:
                line = response.readline(1024 * 1024 + 1)
                received += len(line)
                if received > 64 * 1024**2 or len(line) > 1024 * 1024:
                    raise ProbeError("chat stream exceeded its byte bound")
                if not line:
                    break
                output.write(line)
                output.flush()
                stripped = line.strip()
                if not stripped or stripped.startswith(b":"):
                    continue
                if not stripped.startswith(b"data:"):
                    raise ProbeError("unexpected stream framing")
                data = stripped[5:].strip()
                if data == b"[DONE]":
                    self.done = True
                    break
                self.event(json.loads(data))
        return self.snapshot()

    def event(self, event):
        if not isinstance(event, dict) or event.get("error"):
            raise ProbeError("server returned an invalid stream event")
        if event.get("usage") is not None:
            self.usage = event["usage"]
        if event.get("timings") is not None:
            self.timings = event["timings"]
        choices = event.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ProbeError("expected one streamed choice")
        for choice in choices:
            if choice.get("index", 0) != 0:
                raise ProbeError("unexpected choice index")
            delta = choice.get("delta", {})
            answer = delta.get("content") or ""
            thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if not isinstance(answer, str) or not isinstance(thought, str):
                raise ProbeError("non-text streamed content")
            chunks = delta.get("tool_calls") or []
            if not isinstance(chunks, list):
                raise ProbeError("invalid tool call fragments")
            tool_output = False
            for chunk in chunks:
                index = chunk.get("index")
                if type(index) is not int or not 0 <= index < 16:
                    raise ProbeError("invalid tool call index")
                target = self.calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if chunk.get("id"):
                    if target["id"] and target["id"] != chunk["id"]:
                        raise ProbeError("tool call identity changed")
                    target["id"] = chunk["id"]
                if chunk.get("type", "function") != "function":
                    raise ProbeError("unsupported tool call type")
                for field in ("name", "arguments"):
                    part = (chunk.get("function") or {}).get(field) or ""
                    if not isinstance(part, str):
                        raise ProbeError("non-text tool fragment")
                    target["function"][field] += part
                    tool_output = tool_output or bool(part)
            elapsed = self.clock() - self.started
            if (answer or thought or tool_output) and self.first_token is None:
                self.first_token = elapsed
            if (answer or tool_output) and self.first_answer is None:
                self.first_answer = elapsed
            self.content.append(answer)
            self.reasoning.append(thought)
            if choice.get("finish_reason") is not None:
                self.finish = choice["finish_reason"]

    def snapshot(self):
        return {"message": {"role": "assistant", "content": "".join(self.content),
                    "reasoning": "".join(self.reasoning),
                    "tool_calls": [self.calls[index] for index in sorted(self.calls)]},
                "first_token_seconds": self.first_token, "first_answer_seconds": self.first_answer,
                "service_seconds": self.clock() - self.started if self.started is not None else None,
                "usage": self.usage, "timings": self.timings, "finish_reason": self.finish,
                "stream_complete": self.done and self.finish is not None and self.first_token is not None}
