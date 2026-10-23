"""Kapitsa local-engine observations; no Temper installation support implied.

0.2.9 exposes /health and standard chat usage, including cached tokens.
Its chat API has no native timing or allocation counters: keep those missing.
"""
from ..base import EngineAdapter


class Kapitsa(EngineAdapter):
    helper_limits = {}
