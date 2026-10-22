"""Temper preset interfaces. The lock's preset names one; the workload must match it."""
from ...common import Refusal
from . import chat, rerank

INTERFACES = {"chat-completions": chat, "reranking": rerank}


def resolve(name):
    if name not in INTERFACES:
        raise Refusal(f"Field Kit does not evaluate {name!r} presets yet; see docs/ADAPTERS.md")
    return INTERFACES[name]
