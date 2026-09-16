"""Bounded, typed action proposals for fixed and adaptive questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .catalog import IDENTITY, Refusal, canonical_json, digest
from .watcher import validate_watch_spec


INVESTIGATION_SCHEMA = "field-kit-investigation/v1"
ACTION_SCHEMA = "field-kit-action/v1"


@dataclass(frozen=True)
class Action:
    document: dict[str, Any]
    data: bytes
    sha256: str


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Refusal(f"{label} must be a positive integer")
    return value


def _definitions_by_id(items: object, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise Refusal(f"{label} must be a list")
    definitions: dict[str, dict[str, Any]] = {}
    previous = ""
    for item in items:
        if not isinstance(item, dict):
            raise Refusal(f"{label} entries must be objects")
        identity = item.get("id")
        if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
            raise Refusal(f"{label} contains an invalid ID")
        if identity <= previous:
            raise Refusal(f"{label} must be sorted by unique ID")
        previous = identity
        definitions[identity] = item
    return definitions


def _validate_parameter_definition(item: dict[str, Any]) -> bool:
    identity = item["id"]
    parameter_type = item.get("type")
    if parameter_type == "integer":
        if set(item) != {"id", "type", "minimum", "maximum", "step", "default"}:
            raise Refusal(f"integer parameter {identity!r} has missing or unknown fields")
        minimum = item.get("minimum")
        maximum = item.get("maximum")
        step = item.get("step")
        default = item.get("default")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (minimum, maximum, step, default)
        ):
            raise Refusal(f"integer parameter {identity!r} requires integer bounds, step, and default")
        if (
            step <= 0
            or minimum > maximum
            or not minimum <= default <= maximum
            or (default - minimum) % step
        ):
            raise Refusal(f"integer parameter {identity!r} has inconsistent bounds or default")
        return minimum != maximum
    if parameter_type == "enum":
        if set(item) != {"id", "type", "choices", "default"}:
            raise Refusal(f"enum parameter {identity!r} has missing or unknown fields")
        choices = item.get("choices")
        default = item.get("default")
        if (
            not isinstance(choices, list)
            or not choices
            or any(not isinstance(value, str) or not value for value in choices)
            or choices != sorted(set(choices))
            or default not in choices
        ):
            raise Refusal(f"enum parameter {identity!r} has invalid choices or default")
        return len(choices) > 1
    raise Refusal(f"parameter {identity!r} has unsupported type {parameter_type!r}")


def _validate_values(
    values: object,
    parameter_ids: Sequence[str],
    parameters: dict[str, dict[str, Any]],
    label: str,
) -> dict[str, int | str]:
    if not isinstance(values, dict) or set(values) != set(parameter_ids):
        raise Refusal(f"{label} parameters do not exactly match the action declaration")
    validated: dict[str, int | str] = {}
    for identity in parameter_ids:
        definition = parameters[identity]
        value = values.get(identity)
        if definition["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise Refusal(f"parameter {identity!r} must be an integer")
            if not definition["minimum"] <= value <= definition["maximum"]:
                raise Refusal(
                    f"parameter {identity!r} is outside the approved range "
                    f"{definition['minimum']}..{definition['maximum']}"
                )
            if (value - definition["minimum"]) % definition["step"]:
                raise Refusal(f"parameter {identity!r} is outside the approved integer lattice")
        else:
            if value not in definition["choices"]:
                raise Refusal(f"parameter {identity!r} is not an approved enum value")
        validated[identity] = value
    return validated


def validate_investigation(value: object, question_kind: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema", "parameters", "actions", "initial_action",
        "final_validation_action", "action_selection", "total_attempts_max",
        "total_runtime_minutes_max",
    }:
        raise Refusal("investigation has missing or unknown fields")
    if value.get("schema") != INVESTIGATION_SCHEMA:
        raise Refusal("investigation has an unsupported schema")
    parameters = _definitions_by_id(value.get("parameters"), "investigation.parameters")
    varying = any(_validate_parameter_definition(item) for item in parameters.values())
    actions = _definitions_by_id(value.get("actions"), "investigation.actions")
    if not actions:
        raise Refusal("investigation.actions must not be empty")
    total_attempts = _positive_integer(value.get("total_attempts_max"), "investigation.total_attempts_max")
    total_runtime = _positive_integer(
        value.get("total_runtime_minutes_max"),
        "investigation.total_runtime_minutes_max",
    )
    for identity, action in actions.items():
        if set(action) != {
            "id", "kind", "parameters", "attempts_max", "runtime_minutes_max", "steps",
        }:
            raise Refusal(f"action {identity!r} has missing or unknown fields")
        if action.get("kind") not in {"measurement", "final-validation"}:
            raise Refusal(f"action {identity!r} has unsupported kind")
        parameter_ids = action.get("parameters")
        if (
            not isinstance(parameter_ids, list)
            or any(not isinstance(item, str) for item in parameter_ids)
            or parameter_ids != sorted(set(parameter_ids))
            or any(item not in parameters for item in parameter_ids)
        ):
            raise Refusal(f"action {identity!r} has invalid parameter references")
        maximum = _positive_integer(action.get("attempts_max"), f"action {identity!r} attempts_max")
        if maximum > total_attempts:
            raise Refusal(f"action {identity!r} exceeds the total attempt ceiling")
        runtime = _positive_integer(
            action.get("runtime_minutes_max"),
            f"action {identity!r} runtime_minutes_max",
        )
        if runtime > total_runtime:
            raise Refusal(f"action {identity!r} exceeds the total runtime ceiling")
        steps = action.get("steps")
        if not isinstance(steps, list) or not steps:
            raise Refusal(f"action {identity!r} requires at least one execution step")
        step_ids: set[str] = set()
        available_artifacts: set[str] = set()
        step_runtime = 0
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or set(step) != {
                "id", "kind", "runtime_minutes_max", "output_bytes_max",
                "evidence_bytes_max", "consumes", "produces", "process_watch",
            }:
                raise Refusal(f"action {identity!r} has an invalid execution step")
            step_id = step.get("id")
            if (
                not isinstance(step_id, str)
                or not IDENTITY.fullmatch(step_id)
                or step_id in step_ids
            ):
                raise Refusal(f"action {identity!r} has an invalid or duplicate step ID")
            step_ids.add(step_id)
            step_kind = step.get("kind")
            if step_kind not in {"preparation", "measurement"}:
                raise Refusal(f"action {identity!r} step {step_id!r} has an unsupported kind")
            if index == len(steps) - 1:
                if step_kind != "measurement":
                    raise Refusal(f"action {identity!r} must end with a measurement step")
            elif step_kind != "preparation":
                raise Refusal(f"action {identity!r} may only measure in its final step")
            process_watch = step.get("process_watch")
            if process_watch is not None:
                if step_kind != "measurement":
                    raise Refusal(f"action {identity!r} preparation step cannot watch processes")
                validate_watch_spec(process_watch)
            step_runtime += _positive_integer(
                step.get("runtime_minutes_max"),
                f"action {identity!r} step {step_id!r} runtime_minutes_max",
            )
            output_bytes = step.get("output_bytes_max")
            if isinstance(output_bytes, bool) or not isinstance(output_bytes, int) or output_bytes < 0:
                raise Refusal(
                    f"action {identity!r} step {step_id!r} output_bytes_max "
                    "must be a non-negative integer"
                )
            evidence_bytes = step.get("evidence_bytes_max")
            if (
                isinstance(evidence_bytes, bool)
                or not isinstance(evidence_bytes, int)
                or evidence_bytes < output_bytes
            ):
                raise Refusal(
                    f"action {identity!r} step {step_id!r} evidence_bytes_max "
                    "must cover its artifact output ceiling"
                )
            consumes = _artifact_names(step.get("consumes"), f"action {identity!r} step {step_id!r} consumes")
            produces = _artifact_names(step.get("produces"), f"action {identity!r} step {step_id!r} produces")
            missing = set(consumes) - available_artifacts
            if missing:
                raise Refusal(
                    f"action {identity!r} step {step_id!r} consumes unavailable artifacts"
                )
            if set(produces) & available_artifacts:
                raise Refusal(f"action {identity!r} produces an artifact name more than once")
            if step_kind == "measurement" and produces:
                raise Refusal(f"action {identity!r} measurement step cannot produce artifacts")
            available_artifacts.update(produces)
        if step_runtime > runtime:
            raise Refusal(f"action {identity!r} step time exceeds its action runtime ceiling")
    initial = value.get("initial_action")
    if not isinstance(initial, dict) or set(initial) != {"id", "parameters"} or initial.get("id") not in actions:
        raise Refusal("investigation.initial_action is invalid")
    initial_definition = actions[initial["id"]]
    _validate_values(
        initial.get("parameters"),
        initial_definition["parameters"],
        parameters,
        "initial action",
    )
    final_action = value.get("final_validation_action")
    if final_action not in actions:
        raise Refusal("investigation.final_validation_action is unknown")
    if question_kind == "bounded-adaptive" and not varying:
        raise Refusal("bounded-adaptive questions require at least one varying parameter")
    if question_kind == "fixed" and varying:
        raise Refusal("fixed questions cannot declare a varying parameter")
    selection = value.get("action_selection")
    if question_kind == "fixed" and selection != "fixed":
        raise Refusal("fixed questions require fixed action selection")
    if question_kind == "bounded-adaptive" and selection not in {
        "operator-bounded", "result-directed",
    }:
        raise Refusal("bounded-adaptive questions require a supported action selection policy")
    if question_kind == "bounded-adaptive":
        final_actions = [
            identity for identity, action in actions.items()
            if action["kind"] == "final-validation"
        ]
        if (
            initial_definition["kind"] != "measurement"
            or final_actions != [final_action]
            or final_action == initial["id"]
        ):
            raise Refusal("bounded-adaptive questions require one distinct final-validation action")
    if question_kind == "fixed" and final_action != initial["id"]:
        raise Refusal("fixed questions validate the exact initial action")
    return value


def _artifact_names(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not IDENTITY.fullmatch(item) for item in value)
        or value != sorted(set(value))
    ):
        raise Refusal(f"{label} must be a sorted unique artifact-name list")
    return value


def propose_action(
    investigation: dict[str, Any],
    request: object,
    prior_attempts: Sequence[dict[str, Any]],
) -> Action:
    """Validate one proposed next action without executing or recording it."""
    if not isinstance(request, dict) or set(request) != {"id", "parameters", "reason"}:
        raise Refusal("action proposal has missing or unknown fields")
    identity = request.get("id")
    reason = request.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise Refusal("action proposal requires a concise non-empty reason")
    parameters = _definitions_by_id(investigation["parameters"], "investigation.parameters")
    actions = _definitions_by_id(investigation["actions"], "investigation.actions")
    if identity not in actions:
        raise Refusal(f"action {identity!r} is outside the approved investigation")
    relevant = [
        attempt for attempt in prior_attempts
        if isinstance(attempt, dict) and attempt.get("kind") == "question-action"
    ]
    if len(relevant) >= investigation["total_attempts_max"]:
        raise Refusal("the approved total question-action attempt ceiling is exhausted")
    same_action = [attempt for attempt in relevant if attempt.get("action", {}).get("id") == identity]
    definition = actions[identity]
    if len(same_action) >= definition["attempts_max"]:
        raise Refusal(f"the approved attempt ceiling for action {identity!r} is exhausted")
    values = _validate_values(
        request.get("parameters"),
        definition["parameters"],
        parameters,
        "proposed action",
    )
    previous = relevant[-1].get("candidate", {}) if relevant else {}
    if not isinstance(previous, dict):
        previous = {}
    changes = {
        key: {"from": previous.get(key), "to": value}
        for key, value in values.items()
        if previous.get(key) != value
    }
    document = {
        "schema": ACTION_SCHEMA,
        "attempt": len(relevant) + 1,
        "id": identity,
        "kind": definition["kind"],
        "parameters": values,
        "changes": changes,
        "reason": reason,
    }
    data = canonical_json(document)
    return Action(document, data, digest(data))


def validate_action_candidate(
    investigation: dict[str, Any],
    value: object,
) -> dict[str, Any]:
    """Validate one result-directed next action without consuming a budget."""
    if not isinstance(value, dict) or set(value) != {"id", "parameters"}:
        raise Refusal("next action candidate has missing or unknown fields")
    actions = _definitions_by_id(investigation["actions"], "investigation.actions")
    identity = value.get("id")
    if identity not in actions:
        raise Refusal(f"next action {identity!r} is outside the approved investigation")
    parameters = _definitions_by_id(
        investigation["parameters"],
        "investigation.parameters",
    )
    definition = actions[identity]
    values = _validate_values(
        value.get("parameters"),
        definition["parameters"],
        parameters,
        "next action",
    )
    return {"id": identity, "parameters": values}


def initial_action_request(investigation: dict[str, Any]) -> dict[str, Any]:
    initial = investigation["initial_action"]
    return {
        "id": initial["id"],
        "parameters": initial["parameters"],
        "reason": "package-declared initial action",
    }
