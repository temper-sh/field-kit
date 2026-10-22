"""chat/v1: streamed chat completions through Temper's router.

The raw stream is kept unchanged; the model format decodes it; the engine
adapter reads native rates. Wrong or truncated answers stay measured.
"""
import copy
import json
from ...common import Refusal
from ...stream import ChatStream

IDENTIFIER = "chat/v1"
PATH = "/v1/chat/completions"
EXPECT = ("json", "text", "tool")


def validate_request(request):
    if not isinstance(request, dict) or not isinstance(request.get("messages"), list) or not request["messages"]:
        raise Refusal("step needs a chat-completions request")
    if "model" in request or "stream" in request or "stream_options" in request:
        raise Refusal("Field Kit supplies model and streaming controls")


def build(step, preset, model, previous):
    """The step's request, after replaying the previous turn in the model's own history format."""
    request = copy.deepcopy(step["request"])
    if step.get("append_previous"):
        previous_request, previous_response = previous
        request["messages"] = previous_request["messages"] + [model.assistant_message(previous_response)] + request["messages"]
    return request, {**request, "model": preset, "stream": True, "stream_options": {"include_usage": True}}


def send(engine, files, body, timeout):
    """Return the response and any transport failure; a failure keeps the partial stream."""
    stream = ChatStream(files.raw)
    try:
        return engine.call(PATH, body, timeout, streaming=True, stream_reader=stream), None
    except (Exception, KeyboardInterrupt) as error:
        return stream.snapshot(), error


def observe(response, body, step, engine, model):
    """Decode with the model format and read measurements. Quality fields never erase rates."""
    response = model.normalize(response, body)
    return response, {**engine.metrics(response), "elapsed_seconds": response.get("service_seconds"),
        "first_token_seconds": response.get("first_token_seconds"),
        "first_answer_seconds": response.get("first_answer_seconds"),
        "response_complete": response.get("stream_complete"),
        "finish_reason": response.get("finish_reason"),
        "correct": correct(response, step.get("expect")),
        "model_format": response["model_format"]}


def correct(response, expected):
    """One narrow semantic check, or None when the step names none or output was undecodable."""
    if expected is None or response.get("model_format", {}).get("state") in ("invalid", "partial"):
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
