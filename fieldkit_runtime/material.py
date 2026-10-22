"""Approval rule for variants: settings may change, approved material may not."""
from __future__ import annotations
from typing import Any
from . import lockview
from .common import Refusal

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
