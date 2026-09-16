"""Canonical, read-only planning for Field Kit questions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import MachineFacts, QuestionPackage, Refusal, canonical_json, digest


PLAN_SCHEMA = "field-kit-plan/v1"
PROBE_LISTEN = "127.0.0.1:18080"

# Field Kit owns the workflow. Question packages own what is measured.
SETUP_STAGES = [
    {"id": "01-install-software", "operation": "software-install"},
    {"id": "02-fetch-model", "operation": "model-fetch"},
    {"id": "03-apply-config", "operation": "config-apply"},
    {"id": "04-check-software", "operation": "software-check"},
    {"id": "05-check-artifacts", "operation": "artifact-check"},
    {"id": "06-bind-material", "operation": "material-bind"},
]


@dataclass(frozen=True)
class Plan:
    document: dict[str, Any]
    data: bytes
    sha256: str


def normalize_root(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    if absolute == Path(absolute.anchor) or absolute != Path(os.path.normpath(absolute)):
        raise Refusal("root must be a clean absolute dedicated path other than filesystem root")
    return absolute


def planned_paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "plan": Path(str(root) + ".plan.json"),
        "session": Path(str(root) + ".session.json"),
        "lock": Path(str(root) + ".session.lock"),
        "report": Path(str(root) + ".report.md"),
        "evidence": Path(str(root) + ".evidence"),
    }


def build_plan(
    entry: QuestionPackage,
    facts: MachineFacts,
    facts_data: bytes,
    root_path: Path,
    outcome: str,
    temper: dict[str, str],
    runtime: dict[str, str],
) -> Plan:
    """Build an exact plan without creating a file or directory."""
    if outcome not in {"keep", "restore"}:
        raise Refusal("outcome must be keep or restore")
    allowed = entry.package["consent"].get("allowed_outcomes", ["keep", "restore"])
    if outcome not in allowed:
        raise Refusal("this question permits only these outcomes: " + ", ".join(allowed))
    applicable, reasons = entry.applicable(facts)
    if not applicable:
        raise Refusal("package is not applicable: " + "; ".join(reasons))
    root = normalize_root(root_path)
    paths = planned_paths(root)
    if not root.parent.is_dir():
        raise Refusal(f"root parent is not an existing directory: {root.parent}")
    for label, path in paths.items():
        if path.exists() or path.is_symlink():
            raise Refusal(f"{label} path already exists: {path}")
    files = [
        {"path": relative, "sha256": digest(data), "bytes": len(data)}
        for relative, data in sorted(entry.files.items())
    ]
    document: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "question": {
            "selector": entry.selector,
            "package_sha256": entry.package_sha256,
            "question": entry.package["question"],
            "decision": entry.package["decision"],
            "kind": entry.package["kind"],
            "model": entry.package["profile"]["layout"],
        },
        "machine": {
            "facts_sha256": digest(facts_data),
            "facts": facts.document,
        },
        "host": {
            "temper": temper,
            "field_kit_runtime": runtime,
        },
        "execution": {
            "outcome": outcome,
            "paths": {key: str(value) for key, value in paths.items()},
            "preparation": "temper-execution-export-and-verify",
            "listeners": [PROBE_LISTEN],
            "setup_stages": SETUP_STAGES,
            "question_actions": {
                "initial": entry.package["investigation"]["initial_action"],
                "final_validation": entry.package["investigation"]["final_validation_action"],
            },
            "cleanup": {
                "owner": "field-kit-runtime",
                "operation": "keep" if outcome == "keep" else "temper-software-remove-then-owned-root-remove",
            },
        },
        "inputs": {
            "package": {
                "sha256": entry.package_sha256,
                "bytes": len(entry.package_data),
            },
            "files": files,
        },
        "investigation": entry.package["investigation"],
        "cost_ceiling": entry.package["cost"],
        "effects": entry.package["consent"],
        "invalidation_triggers": entry.package["invalidation_triggers"],
    }
    data = canonical_json(document)
    return Plan(document, data, digest(data))


def load_plan(path: Path) -> Plan:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"plan is not a regular file: {path}")
    data = path.read_bytes()
    if len(data) > 1024 * 1024:
        raise Refusal("plan exceeds 1 MiB")
    try:
        document = json.loads(data)
    except json.JSONDecodeError as error:
        raise Refusal(f"invalid plan JSON: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != PLAN_SCHEMA or canonical_json(document) != data:
        raise Refusal("plan is not canonical field-kit-plan/v1")
    return Plan(document, data, digest(data))
