"""Compare approved Temper material; Temper remains the lock compiler."""
from __future__ import annotations
import copy
from typing import Any
from .common import Refusal

def frozen_material(lock: dict[str, Any]) -> dict[str, Any]:
    """Exclude only the tuning controls explicitly covered by study consent.

    This reads Temper's published lock contract; it never constructs a lock.
    Software, artifacts, template, engine and all other fields stay exact.
    """
    result = copy.deepcopy(lock)
    selected = result["records"]["presets"].pop(result["preset"])
    result["preset"] = "selected"
    selected.pop("presentation", None)
    selected.pop("request_defaults", None)
    selected.pop("speculation", None)
    selected.pop("compute", None)
    for profile in selected["profiles"]:
        profile.pop("context_window_tokens", None)
        profile.pop("engine_config", None)
    result["records"]["presets"]["selected"] = selected
    result["records"].pop("date", None)
    result["records"].pop("presentation", None)
    return result


def validate_variant(base: dict[str, Any], variant: dict[str, Any], maximum_context: int) -> None:
    fixed_base, fixed_variant = frozen_material(base), frozen_material(variant)
    approved = fixed_base["records"].get("artifacts", {})
    selected = fixed_variant["records"].get("artifacts", {})
    # Turning speculation off drops its unused sidecar from Temper's closure.
    # Omitting approved material grants no new access; adding or changing it does.
    if any(key not in approved or value != approved[key] for key, value in selected.items()):
        raise Refusal("variant changes approved weights or draft; it needs a new study plan")
    fixed_base["records"]["artifacts"] = selected
    if fixed_base != fixed_variant:
        raise Refusal("variant changes approved engine/software/weights/template or other fixed material; it needs a new study plan")
    selected = variant["records"]["presets"][variant["preset"]]
    for profile in selected["profiles"]:
        if not 0 < profile["context_window_tokens"] <= maximum_context:
            raise Refusal("variant exceeds the approved context ceiling")
    output = selected.get("request_defaults", {}).get("max_output_tokens", 0)
    if output > maximum_context:
        raise Refusal("variant exceeds the approved output ceiling")
