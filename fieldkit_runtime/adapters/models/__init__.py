"""Explicit format selection; model names, weights and presets stay in Temper."""
from ...common import Refusal
from .flint import Flint
from .openai import OpenAI

DEFAULT = "openai/v1"
ADAPTERS = {DEFAULT: OpenAI, "flint/v1": Flint}


def resolve(identifier=DEFAULT):
    if not isinstance(identifier, str) or identifier not in ADAPTERS:
        raise Refusal(f"unknown model adapter {identifier!r}; see docs/ADAPTERS.md")
    return ADAPTERS[identifier](identifier)
