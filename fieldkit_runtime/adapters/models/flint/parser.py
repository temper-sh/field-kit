"""Flint's tagged tool arguments, using request schemas to preserve value types.

This is a decoder, not a tool executor or an output repairer. JSON containers
are consumed by the JSON decoder, so tag text inside their strings is data.
Top-level strings use Flint's literal text representation.
"""
import json
import re

from ..openai import FormatError

NAME = re.compile(r"[A-Za-z0-9_.-]+")


class TaggedCalls:
    def __init__(self, text, tools, identity):
        self.text, self.position = text, 0
        self.identity = identity
        self.functions = {}
        if not isinstance(tools, list):
            raise FormatError("request tools must be a list")
        for tool in tools:
            function = tool.get("function", {}) if isinstance(tool, dict) else {}
            if isinstance(tool, dict) and tool.get("type") == "function" and isinstance(function, dict) and isinstance(function.get("name"), str):
                if function["name"] in self.functions:
                    raise FormatError("duplicate function schema")
                self.functions[function["name"]] = function

    def whitespace(self):
        while self.position < len(self.text) and self.text[self.position].isspace():
            self.position += 1

    def take(self, token):
        if not self.text.startswith(token, self.position):
            raise FormatError(f"expected {token} at offset {self.position}")
        self.position += len(token)

    def until(self, token):
        end = self.text.find(token, self.position)
        if end < 0:
            raise FormatError(f"missing {token}")
        value, self.position = self.text[self.position:end], end + len(token)
        return value

    def argument(self, schema):
        kind = schema.get("type") if isinstance(schema, dict) else None
        if kind == "string":
            return self.until("</arg_value>")
        if kind not in ("object", "array", "integer", "number", "boolean", "null"):
            raise FormatError("tagged argument needs an explicit supported type in the request tool schema")
        self.whitespace()
        try:
            value, end = json.JSONDecoder().raw_decode(self.text, self.position)
        except ValueError as error:
            raise FormatError("invalid JSON in tagged argument") from error
        # Do not coerce e.g. true to 1 or a JSON-looking string to an array.
        valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
                 "integer": type(value) is int, "number": type(value) in (int, float),
                 "boolean": type(value) is bool, "null": value is None}[kind]
        if not valid:
            raise FormatError("tagged argument does not match its declared type")
        self.position = end
        self.whitespace()
        self.take("</arg_value>")
        return value

    def parse(self):
        calls = []
        while self.position < len(self.text):
            self.whitespace()
            if self.position == len(self.text):
                break
            if len(calls) == 16:
                raise FormatError("too many tagged calls")
            self.take("<tool_call>")
            name = NAME.match(self.text, self.position)
            if not name:
                raise FormatError("missing tagged function name")
            self.position = name.end()
            name = name.group()
            if name not in self.functions:
                raise FormatError("tagged function has no request tool schema: " + name)
            parameters = self.functions[name].get("parameters") or {}
            if not isinstance(parameters, dict):
                raise FormatError("request tool parameters must be an object")
            properties = parameters.get("properties") or {}
            if not isinstance(properties, dict):
                raise FormatError("request tool properties must be an object")
            arguments = {}
            self.whitespace()
            while not self.text.startswith("</tool_call>", self.position):
                self.take("<arg_key>")
                key = self.until("</arg_key>")
                if key in arguments:
                    raise FormatError("duplicate tagged argument: " + key)
                self.whitespace()
                self.take("<arg_value>")
                arguments[key] = self.argument(properties.get(key))
                self.whitespace()
            self.take("</tool_call>")
            try:
                encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
            except ValueError as error:
                raise FormatError("non-finite JSON in tagged arguments") from error
            calls.append({"id": f"field-kit-{self.identity}-{len(calls)}", "type": "function",
                          "function": {"name": name, "arguments": encoded}})
        return calls
