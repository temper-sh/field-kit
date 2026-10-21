"""Reusable request inputs; studies need data and knowledge, not new runners."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .common import Refusal, IDENTITY, load_json

def validate(document):
    if not isinstance(document, dict) or not isinstance(document.get("question"), str) or not document["question"].strip():
        raise Refusal("workload needs a question")
    if "command" in document:
        if any(key in document for key in ("request", "sessions", "steps")):
            raise Refusal("choose a command or requests, not both")
        command = document["command"]
        if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
            raise Refusal("command must be a nonempty argv list, without a shell")
        if not isinstance(document.get("cwd"), str) or not Path(document["cwd"]).is_dir():
            raise Refusal("command workload needs an existing cwd")
        return document
    if "request" in document:
        sessions = [{"id": "request", "steps": [{"id": "request", "request": document["request"],
                     **({"expect": document["expect"]} if "expect" in document else {})}]}]
    elif "steps" in document:
        sessions = [{"id": "session", "steps": document["steps"]}]
    else:
        sessions = document.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise Refusal("workload needs a request, steps, sessions or command")
    seen = set()
    for session in sessions:
        identity = session.get("id") if isinstance(session, dict) else None
        if not isinstance(identity, str) or not IDENTITY.fullmatch(identity) or identity in seen:
            raise Refusal("sessions need unique plain IDs")
        seen.add(identity)
        steps = session.get("steps")
        if not isinstance(steps, list) or not steps:
            raise Refusal("session needs request steps")
        names = set()
        for index, step in enumerate(steps):
            name = step.get("id") if isinstance(step, dict) else None
            if not isinstance(name, str) or not IDENTITY.fullmatch(name) or name in names:
                raise Refusal("steps need unique plain IDs")
            names.add(name)
            request = step.get("request")
            if not isinstance(request, dict) or not isinstance(request.get("messages"), list) or not request["messages"]:
                raise Refusal("step needs a chat-completions request")
            if "model" in request or "stream" in request or "stream_options" in request:
                raise Refusal("Field Kit supplies model and streaming controls")
            if step.get("append_previous") and not index:
                raise Refusal("append_previous needs a preceding response")
            expected = step.get("expect")
            if expected is not None and (not isinstance(expected, dict) or len(expected) != 1
                                         or next(iter(expected)) not in ("json", "text", "tool")):
                raise Refusal("expect is one of json, text or tool")
    return {**document, "sessions": sessions}

def load(path):
    document = load_json(path, 64 * 1024**2)
    if "cwd" in document:
        document["cwd"] = str((path.parent / document["cwd"]).resolve())
    return validate(document)

def correct(response, expected):
    if expected is None:
        return None
    message = response.get("message") or {}
    try:
        if "json" in expected:
            return json.loads(message.get("content") or "") == expected["json"]
        if "text" in expected:
            return (message.get("content") or "").strip() == expected["text"]
        calls = message.get("tool_calls") or []
        return len(calls) == 1 and calls[0]["function"]["name"] == expected["tool"]["name"] and \
            json.loads(calls[0]["function"]["arguments"]) == expected["tool"]["arguments"]
    except (ValueError, TypeError, KeyError, IndexError):
        return False

def generate(kind, records=256, output_tokens=512):
    if not 8 <= records <= 32768 or not 32 <= output_tokens <= 32768:
        raise Refusal("use 8–32768 records and 32–32768 output tokens")
    if kind in ("smoke", "tools"):
        body = {"messages": [{"role": "user", "content": "Reply with exactly FIELD_KIT_OK."}], "max_tokens": output_tokens}
        expect = {"text": "FIELD_KIT_OK"}
        if kind == "tools":
            body["messages"][0]["content"] = "Call record_answer with answer FIELD_KIT_OK."
            body["tools"] = [{"type": "function", "function": {"name": "record_answer",
                "description": "Record an answer.", "parameters": {"type": "object",
                "properties": {"answer": {"type": "string"}}, "required": ["answer"],
                "additionalProperties": False}}}]
            expect = {"tool": {"name": "record_answer", "arguments": {"answer": "FIELD_KIT_OK"}}}
        return validate({"question": "Does this configuration answer a cheap " + kind + " request?",
                         "request": body, "expect": expect})
    if kind == "engine":
        if records < 64:
            raise Refusal("engine screen needs at least 64 records")
        indices = [i * (records - 1) // 31 for i in range(32)]
        answers = [hashlib.sha256(f"engine-v2:{i}".encode()).hexdigest()[:16] for i in indices]
        values = dict(zip(indices, answers))
        question = "Return only a JSON array of the values of records " + ", ".join(map(str, indices)) + ", in order."
        sessions = []
        for name, selected in (("short", indices), ("populated", range(records))):
            content = "\n".join(f"record {i}: {values.get(i, 'unused-' + str(i))}" for i in selected)
            sessions.append({"id": name, "steps": [{"id": name, "request": {
                "messages": [{"role": "user", "content": content + "\n" + question}],
                "max_tokens": output_tokens}, "expect": {"json": answers}}]})
        return validate({"question": "Compare the same work at short and populated input, each cold.",
                         "input_records": records, "sessions": sessions})
    if kind not in ("context", "cache"):
        raise Refusal("unknown generated workload")
    values = [hashlib.sha256(f"field-kit-context-v1:{i}".encode()).hexdigest()[:16] for i in range(records)]
    indices = [0, records // 2, records - 1]
    expected = [values[i] for i in indices]
    text = "\n".join(f"record {i:05d}: {value}" for i, value in enumerate(values))
    question = "Return only a JSON array of the values of records " + ", ".join(map(str, indices)) + "."
    messages = [{"role": "system", "content": "Look up records exactly. Return JSON."},
                {"role": "user", "content": text + "\n" + question}]
    request = {"messages": messages, "max_tokens": output_tokens}
    steps = [{"id": "cold", "request": request, "expect": {"json": expected}},
             {"id": "append", "request": {**request, "messages": [{"role": "user", "content": question}]},
              "append_previous": True, "expect": {"json": expected}}]
    if kind == "cache":
        replacement = "changed-prefix-01"
        revised = [{**row} for row in messages]
        revised[1]["content"] = revised[1]["content"].replace(values[0], replacement)
        steps.append({"id": "early-edit", "request": {**request, "messages": revised},
                      "expect": {"json": [replacement, *expected[1:]]}})
    return validate({"question": "Observe populated input, continuation" + (" and early-edit invalidation." if kind == "cache" else "."),
                     "input_records": records, "steps": steps})

def health_path(family):
    return "/health/ready" if family == "rapid-mlx" else "/health"
