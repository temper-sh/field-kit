"""Small, fixed questions for the Qwen study; no model or engine selection here."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .common import Refusal

KINDS = ("smoke", "coding", "context", "cache")


def cases(coding: list[dict[str, Any]], kind: str, *, case_id: str | None = None,
          records: int = 256, output_tokens: int = 512) -> list[dict[str, Any]]:
    if kind not in KINDS or type(records) is not int or not 8 <= records <= 32768:
        raise Refusal("choose smoke, coding, context or cache, with 8–32768 input records")
    if type(output_tokens) is not int or not 32 <= output_tokens <= 32768:
        raise Refusal("the output allowance must be 32–32768 tokens")
    if kind == "coding":
        selected = [row for row in coding if not case_id or row["id"] == case_id]
        if not selected:
            raise Refusal(f"unknown coding case: {case_id}")
        return selected
    if case_id:
        raise Refusal("--case applies only to coding")
    if kind == "smoke":
        request = {
            "messages": [{"role": "user", "content": "Call record_answer with answer FIELD_KIT_OK. Do not write prose."}],
            "tools": [{"type": "function", "function": {"name": "record_answer", "description": "Record the answer.",
                "parameters": {"type": "object", "properties": {"answer": {"type": "string"}},
                               "required": ["answer"], "additionalProperties": False}}}],
            "tool_choice": "auto", "max_tokens": output_tokens,
        }
        return [{"id": "smoke", "request": request, "oracle": {"answer": "FIELD_KIT_OK", "tool": True}}]
    # Record counts are input sizes, never claimed to be tokenizer counts.
    # Multiple positions and changed-prefix queries catch stale cache answers.
    values = [hashlib.sha256(f"field-kit-context-v1:{i}".encode()).hexdigest()[:16] for i in range(records)]
    indices = [0, records // 2, records - 1]
    prompt = "\n".join(f"record {i:05d}: {value}" for i, value in enumerate(values))
    question = "Return only a JSON array of the values of records " + ", ".join(map(str, indices)) + "."
    messages = [{"role": "system", "content": "Look up records exactly. Return JSON without commentary."},
                {"role": "user", "content": prompt + "\n" + question}]
    expected = [values[i] for i in indices]
    request = {"messages": messages, "max_tokens": output_tokens}
    steps = [{"id": "cold", "request": request, "oracle": {"answer": expected}}]
    continuation = [*messages, {"role": "assistant", "content": json.dumps(expected)},
                    {"role": "user", "content": question}]
    steps.append({"id": "append", "request": {**request, "messages": continuation}, "oracle": {"answer": expected}})
    if kind == "cache":
        replacement = "changed-prefix-01"
        revised = [{**message} for message in messages]
        revised[1]["content"] = revised[1]["content"].replace(values[0], replacement)
        steps.append({"id": "rewind", "request": {**request, "messages": revised},
                      "oracle": {"answer": [replacement, *expected[1:]]}})
    return [{"id": kind, "steps": steps, "input_records": records, "output_allowance_tokens": output_tokens}]


def correct(response: dict[str, Any], oracle: dict[str, Any]) -> tuple[bool, str]:
    """Semantic answer checks; engine/stream failures are classified elsewhere."""
    message = response.get("message") or {}
    try:
        if oracle.get("tool"):
            calls = message.get("tool_calls") or []
            if len(calls) != 1 or calls[0].get("function", {}).get("name") != "record_answer":
                return False, "expected one real record_answer tool call"
            answer = json.loads(calls[0]["function"]["arguments"])
            return answer == {"answer": oracle["answer"]}, "tool arguments must contain the requested answer"
        answer = json.loads(message.get("content") or "")
        return answer == oracle["answer"], "record lookup must match all requested positions"
    except (ValueError, KeyError, TypeError):
        return False, "answer was not valid JSON for this question"


def health_path(family: str) -> str:
    return "/health/ready" if family == "rapid-mlx" else "/health"
