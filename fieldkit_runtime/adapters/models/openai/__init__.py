"""Already-structured chat-completions messages and lossless tool envelopes."""
from copy import deepcopy

from ....common import Refusal


class FormatError(ValueError):
    """Observed output cannot be interpreted unambiguously in this format."""


class OpenAI:
    reasoning_field = None

    def __init__(self, identifier):
        self.identifier = identifier

    def parse_message(self, message, request, complete):
        return message

    def normalize(self, response, request):
        """Keep wire evidence and parsing observations separate from completion."""
        result = deepcopy(response)
        wire = result["message"]
        report = {"adapter": self.identifier, "state": "unchanged"}
        complete = bool(result.get("stream_complete")) and result.get("finish_reason") in ("stop", "tool_calls")
        try:
            message = self.parse_message(deepcopy(wire), request, complete)
        except FormatError as error:
            report.update(state="invalid" if complete else "partial", issue=str(error))
            result["wire_first_answer_seconds"] = result.get("first_answer_seconds")
            result["first_answer_seconds"] = None
        else:
            if message != wire:
                report["state"] = "decoded"
                result.update(wire_message=wire, message=message)
                # Markup arrived as content. Its first byte does not establish
                # time to an answer or a usable call after format decoding.
                result["wire_first_answer_seconds"] = result.get("first_answer_seconds")
                result["first_answer_seconds"] = None
                if message.get("tool_calls") and not wire.get("tool_calls"):
                    report["tool_call_ids"] = "assigned-by-field-kit"
        result["model_format"] = report
        return result

    def assistant_message(self, response):
        """Serialize this adapter's history. Never guess a missing call identity."""
        if response.get("model_format", {}).get("state") in ("invalid", "partial"):
            raise Refusal("cannot replay undecoded model output; inspect the response and format issue")
        message = response["message"]
        content = message.get("content") or ""
        reasoning = message.get("reasoning", message.get("reasoning_content", "")) or ""
        if not isinstance(content, str) or not isinstance(reasoning, str):
            raise Refusal("cannot replay non-text assistant content or reasoning")
        result = {"role": "assistant", "content": content}
        if self.reasoning_field and reasoning:
            result[self.reasoning_field] = reasoning
        calls = message.get("tool_calls") or []
        identities = set()
        if not isinstance(calls, list):
            raise Refusal("cannot replay ambiguous tool-call envelopes")
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            identity = call.get("id") if isinstance(call, dict) else None
            if (not isinstance(identity, str) or not identity or identity in identities
                    or call.get("type") != "function" or not isinstance(function, dict)
                    or not isinstance(function.get("name"), str) or not function["name"]
                    or not isinstance(function.get("arguments"), str)):
                raise Refusal("cannot replay ambiguous tool-call envelopes")
            identities.add(identity)
        if calls:
            result["tool_calls"] = deepcopy(calls)
        return result
