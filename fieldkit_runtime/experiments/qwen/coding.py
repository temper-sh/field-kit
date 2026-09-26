"""First-attempt coding transport, token budgets and observable performance."""
from __future__ import annotations

import copy
import json
import time
from urllib.parse import quote

from ...probe import ProbeError
from .measurement import monitored_call
from .method import finite


class CodingStream:
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
                    raise ProbeError("coding stream exceeded its byte bound")
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


def native_count(probe, model, family, body, timeout, rapid_count=None):
    native = "/upstream/" + quote(model, safe="")
    request = {key: copy.deepcopy(body[key]) for key in ("messages", "tools", "tool_choice", "chat_template_kwargs", "reasoning_effort") if key in body}
    request["model"] = model
    if family == "rapid-mlx":
        return rapid_count(request)
    if family == "vllm-metal":
        result = monitored_call(probe, native + "/tokenize", request, timeout)
        count = result.get("count")
    else:
        rendered = monitored_call(probe, native + "/apply-template", request, timeout)
        if not isinstance(rendered, dict) or not isinstance(rendered.get("prompt"), str):
            raise ProbeError("native template endpoint returned no prompt")
        result = monitored_call(probe, native + "/tokenize", {"content": rendered["prompt"],
            "add_special": family == "llama", "parse_special": True, "with_pieces": False}, timeout)
        tokens = result.get("tokens")
        if not isinstance(tokens, list) or any(type(token) is not int or token < 0 for token in tokens):
            raise ProbeError("native tokenizer returned no token IDs")
        count = len(tokens)
    if type(count) is not int or count <= 0:
        raise ProbeError("native tokenizer returned no positive count")
    return count


def request_body(case, model, window, input_tokens, family="splash"):
    body = copy.deepcopy(case["request"])
    # Preserve the comparison's total window and allocate its remainder to the
    # first attempt. No repair prompt, response feedback or hidden retry.
    reserve = window - input_tokens
    if reserve < 4096:
        raise ProbeError("task leaves less than 4,096 tokens for a first attempt")
    body.update(model=model, max_tokens=reserve, stream=True, stream_options={"include_usage": True})
    body.update(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, seed=17,
                presence_penalty=0.0, reasoning_effort="medium",
                chat_template_kwargs={"enable_thinking": True, "reasoning_effort": "medium", "preserve_thinking": False})
    body["repeat_penalty" if family in ("splash", "llama") else "repetition_penalty"] = 1.0
    return body


def performance(response, preflight_tokens):
    usage = response.get("usage") or {}
    timings = response.get("timings") or {}
    valid = (response.get("stream_complete") is True and type(usage.get("prompt_tokens")) is int
             and usage["prompt_tokens"] == preflight_tokens and type(usage.get("completion_tokens")) is int
             and usage["completion_tokens"] > 0)
    prefill = decode = None
    if finite(timings.get("prompt_ms"), positive=True) and timings.get("prompt_n") == preflight_tokens:
        prefill = preflight_tokens * 1000 / timings["prompt_ms"]
    if finite(timings.get("predicted_ms"), positive=True) and timings.get("predicted_n") == usage.get("completion_tokens"):
        decode = usage["completion_tokens"] * 1000 / timings["predicted_ms"]
    elapsed, first = response.get("service_seconds"), response.get("first_token_seconds")
    observed = None
    if valid and finite(elapsed, positive=True) and finite(first) and elapsed > first:
        observed = max(0, usage["completion_tokens"] - 1) / (elapsed - first)
    return {"measurement_valid": valid, "native_input_tokens": preflight_tokens,
            "prefill_tokens_per_second": prefill, "generation_tokens_per_second": decode,
            "observed_output_tokens_per_second": observed,
            "rate_note": "native prefill/decode timings when exposed; observed output rate includes stream overhead and speculative batches"}
