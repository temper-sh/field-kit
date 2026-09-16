"""Typed answer states shared by every Field Kit question."""

from __future__ import annotations

from typing import Any, Sequence

from .catalog import IDENTITY, Refusal


ANSWER_STATES = {"observed", "failed", "not-applicable", "unknown"}


def validate_answer_fields(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise Refusal("report.answer_fields must be a non-empty list")
    fields: list[str] = []
    for item in value:
        if not isinstance(item, str) or not IDENTITY.fullmatch(item):
            raise Refusal("report.answer_fields contains an invalid semantic field ID")
        fields.append(item)
    if fields != sorted(set(fields)):
        raise Refusal("report.answer_fields must be sorted and unique")
    return fields


def validate_answers(value: object, required_fields: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(required_fields):
        raise Refusal("protocol answers do not exactly cover the declared answer fields")
    answers: dict[str, dict[str, Any]] = {}
    for field in required_fields:
        answer = value.get(field)
        if not isinstance(answer, dict) or not set(answer).issubset({"state", "value", "reason"}):
            raise Refusal(f"answer {field!r} has missing or unknown fields")
        state = answer.get("state")
        reason = answer.get("reason")
        if state not in ANSWER_STATES:
            raise Refusal(f"answer {field!r} has an invalid state")
        if state == "observed" and "value" not in answer:
            raise Refusal(f"observed answer {field!r} requires a value")
        if state != "observed" and (not isinstance(reason, str) or not reason.strip()):
            raise Refusal(f"answer {field!r} in state {state!r} requires a reason")
        if "reason" in answer and (not isinstance(reason, str) or not reason.strip()):
            raise Refusal(f"answer {field!r} has an invalid reason")
        answers[field] = answer
    return answers
