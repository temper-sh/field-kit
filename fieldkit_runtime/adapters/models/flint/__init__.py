"""Flint reasoning history and optional decoding of its raw tagged output."""
import hashlib
import json
from ..openai import FormatError, OpenAI
from .parser import TaggedCalls


class Flint(OpenAI):
    reasoning_field = "reasoning_content"

    def parse_message(self, message, request, complete):
        text = message.get("content") or ""
        # Engines that already parse Flint own their structured output. Do not
        # reinterpret tag examples inside ordinary prose or tool arguments.
        if message.get("tool_calls") or not text.lstrip().startswith(("<think>", "<tool_call>")):
            return message
        if not complete:
            raise FormatError("incomplete tagged Flint output retained without actionable calls")
        text = text.lstrip()
        if text.startswith("<think>"):
            end = text.find("</think>", len("<think>"))
            if end < 0:
                raise FormatError("unclosed Flint reasoning block")
            if message.get("reasoning") or message.get("reasoning_content"):
                raise FormatError("both structured and tagged Flint reasoning were returned")
            message["reasoning"] = text[len("<think>"):end]
            text = text[end + len("</think>"):]
        if text.lstrip().startswith("<tool_call>"):
            # Include prior history: repeated identical calls in different turns
            # must still have distinct identities for subsequent tool feedback.
            identity = hashlib.sha256(json.dumps([request, text], sort_keys=True).encode()).hexdigest()[:24]
            message["tool_calls"] = TaggedCalls(text, request.get("tools") or [], identity).parse()
            message["content"] = ""
        else:
            message["content"] = text
        return message
