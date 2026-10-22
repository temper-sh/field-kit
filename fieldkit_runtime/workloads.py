"""Reusable request inputs; studies need data and knowledge, not new runners.

Structure (sessions, steps, a harness command) lives here. What one request
may contain belongs to the preset's interface.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .common import Refusal, IDENTITY, digest, load_json, read_regular
from .adapters import interfaces, models

def validate(document, interface=None):
    interface = interface or interfaces.resolve("chat-completions")
    if not isinstance(document, dict) or not isinstance(document.get("question"), str) or not document["question"].strip():
        raise Refusal("workload needs a question")
    if interface.MODEL_FORMAT:
        document = {**document, "model_adapter": models.resolve(document.get("model_adapter", models.DEFAULT)).identifier}
    elif document.get("model_adapter") is not None:
        raise Refusal(f"{interface.IDENTIFIER} has no model format; remove model_adapter")
    else:
        document = {**document, "model_adapter": None}
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
            interface.validate_request(step.get("request"))
            if step.get("append_previous") and not index:
                raise Refusal("append_previous needs a preceding response")
            if step.get("append_previous") and not interface.MODEL_FORMAT:
                raise Refusal(f"append_previous replays chat history; {interface.IDENTIFIER} has none")
            expected = step.get("expect")
            if expected is not None and (not isinstance(expected, dict) or len(expected) != 1
                                         or next(iter(expected)) not in interface.EXPECT):
                raise Refusal("expect is one of " + ", ".join(interface.EXPECT) if interface.EXPECT
                              else f"{interface.IDENTIFIER} steps take no expect; the suite that owns the judgments grades them")
    return {**document, "sessions": sessions}

def adapter_scope(work):
    """Where the model format applies: requests Field Kit sends, or an external harness's own."""
    if work["model_adapter"] is None:
        return None
    return "external-harness" if "command" in work else "request-workload"

def load(path, *, model_adapter=None, interface=None):
    document = load_json(path, 64 * 1024**2)
    if model_adapter is not None:
        document["model_adapter"] = model_adapter
    if "cwd" in document:
        document["cwd"] = str((path.parent / document["cwd"]).resolve())
    return validate(document, interface)

def replay(path, output_tokens=4096):
    """A timing-only replay of one recorded chat request: the same messages, tools and sampling,
    no prompt cache and a forced output length. It measures sustained speed, never quality.
    The no-cache, ignore-EOS and per-token timing fields are llama.cpp's; check other engines."""
    if not 32 <= output_tokens <= 32768:
        raise Refusal("use 32–32768 output tokens")
    raw = read_regular(Path(path), 64 * 1024**2)
    try:
        request = json.loads(raw)
    except ValueError as error:
        raise Refusal(f"replay needs a recorded JSON chat request: {error}") from error
    if not isinstance(request, dict) or not isinstance(request.get("messages"), list) or not request["messages"]:
        raise Refusal("replay needs a recorded chat-completions request with messages")
    request = {key: value for key, value in request.items()
               if key not in ("model", "stream", "stream_options", "max_completion_tokens")}
    request.update(max_tokens=output_tokens, cache_prompt=False, ignore_eos=True, timings_per_token=True)
    return {"question": f"Timing only: how does decoding hold up over {output_tokens} forced tokens of this "
                        "recorded request? Not a quality check; the forced length ignores the model's own stop.",
            "replay_of": {"path": str(Path(path).absolute()), "sha256": digest(raw)},
            "steps": [{"id": "replay", "request": request}]}

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
