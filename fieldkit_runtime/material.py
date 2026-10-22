"""Approval rule for variants: settings may change, approved material may not."""
from __future__ import annotations
import json
import re
from typing import Any
from . import lockview
from .common import Refusal

MISSING = object()

def arguments_map(arguments: list[str]) -> dict[str, Any]:
    """A local engine's flags and their values; a flag with no value maps to True."""
    is_flag = lambda word: word.startswith("-") and not re.fullmatch(r"-\d+(\.\d+)?", word)  # -1 is a value
    result, index = {}, 0
    while index < len(arguments):
        flag = arguments[index]
        if is_flag(flag) and index + 1 < len(arguments) and not is_flag(arguments[index + 1]):
            result[flag], index = arguments[index + 1], index + 2
        else:
            result[flag], index = True, index + 1
    return result

def setting(row: dict[str, Any], name: str) -> Any:
    """One setting of a candidate: a local engine flag such as -c, or a path in a Temper
    lock's profile such as context_window_tokens or engine_config.kv_cache."""
    if row.get("subject") == "local":
        return arguments_map(json.loads(row["spec"])["arguments"]).get(name, MISSING)
    value: Any = lockview.preset(json.loads(row["lock"]))["profiles"][0]
    for part in name.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value

def require_fixed(base: dict[str, Any], variant: dict[str, Any], names: list[str]) -> None:
    for name in names:
        if setting(variant, name) != setting(base, name):
            raise Refusal(f"variant changes the fixed setting {name}: {setting(base, name)!r} → {setting(variant, name)!r}")

def validate_variant(base: dict[str, Any], variant: dict[str, Any], maximum_context: int) -> None:
    fixed_base, fixed_variant = lockview.frozen_material(base), lockview.frozen_material(variant)
    approved = fixed_base["records"].get("artifacts", {})
    selected = fixed_variant["records"].get("artifacts", {})
    # Turning speculation off drops its unused sidecar from Temper's closure.
    # Omitting approved material grants no new access; adding or changing it does.
    if any(key not in approved or value != approved[key] for key, value in selected.items()):
        raise Refusal("variant changes approved weights or draft; it needs a new study plan")
    fixed_base["records"]["artifacts"] = selected
    if fixed_base != fixed_variant:
        raise Refusal("variant changes approved engine/software/weights/template or other fixed material; it needs a new study plan")
    if not all(0 < window <= maximum_context for window in lockview.context_windows(variant)):
        raise Refusal("variant exceeds the approved context ceiling")
    if lockview.max_output(variant) > maximum_context:
        raise Refusal("variant exceeds the approved output ceiling")
