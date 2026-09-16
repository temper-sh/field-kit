"""Generic immutable artifact envelopes for package-defined action steps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .catalog import Refusal, SHA256, canonical_json, digest


STEP_SCHEMA = "field-kit-action-step/v1"
STEP_RESULT_SCHEMA = "field-kit-action-step-result/v1"
ARTIFACT_SCHEMA = "field-kit-prepared-artifact/v1"
MATERIAL_SCHEMA = "field-kit-prepared-material/v1"
MATERIAL_FILE = "material.json"


@dataclass(frozen=True)
class Step:
    document: dict[str, Any]
    data: bytes
    sha256: str


def build_step(
    *,
    action: dict[str, Any],
    action_sha256: str,
    step: dict[str, Any],
    step_index: int,
    plan_sha256: str,
    inputs: dict[str, dict[str, Any]],
    artifact_root: Path,
) -> Step:
    consumed = {
        name: {
            "root": item["root"],
            "artifact": item["artifact"],
        }
        for name, item in sorted(inputs.items())
        if name in step["consumes"]
    }
    outputs = {
        name: {"root": str(artifact_root / name)}
        for name in step["produces"]
    }
    document = {
        "schema": STEP_SCHEMA,
        "plan_sha256": plan_sha256,
        "action": {
            "id": action["id"],
            "attempt": action["attempt"],
            "sha256": action_sha256,
            "parameters": action["parameters"],
        },
        "step": {
            "id": step["id"],
            "index": step_index,
            "kind": step["kind"],
        },
        "inputs": consumed,
        "outputs": outputs,
        "limits": {
            "runtime_seconds_max": step["runtime_minutes_max"] * 60,
            "output_bytes_max": step["output_bytes_max"],
            "evidence_bytes_max": step["evidence_bytes_max"],
        },
        "process_watch": step["process_watch"],
    }
    data = canonical_json(document)
    return Step(document, data, digest(data))


def validate_step_result(
    report: object,
    *,
    report_data: bytes,
    action_sha256: str,
    step: Step,
    definition: dict[str, Any],
    artifact_root: Path,
    prior_artifacts: dict[str, dict[str, Any]],
    final_step: bool,
) -> tuple[dict[str, dict[str, Any]], bool, object]:
    """Validate the generic envelope and return artifacts, terminal, answers."""
    fields = {
        "schema", "status", "action_sha256", "step_sha256", "outcome",
        "consumed_artifacts", "produced_artifacts", "answers", "protocol",
        "next_actions",
    }
    if (
        not isinstance(report, dict)
        or set(report) != fields
        or canonical_json(report) != report_data
        or report.get("schema") != STEP_RESULT_SCHEMA
        or report.get("status") != "complete"
        or report.get("action_sha256") != action_sha256
        or report.get("step_sha256") != step.sha256
    ):
        raise Refusal("question action step returned an invalid or unbound result envelope")
    expected_consumed = {
        name: prior_artifacts[name]["artifact"]["id"]
        for name in definition["consumes"]
    }
    if report.get("consumed_artifacts") != expected_consumed:
        raise Refusal("question action step did not consume the bound artifact identities")
    outcome = report.get("outcome")
    if outcome not in {"continue", "terminal"}:
        raise Refusal("question action step has an invalid outcome")
    if final_step and outcome != "terminal":
        raise Refusal("final question action step must return a terminal result")
    if outcome == "terminal" and report.get("produced_artifacts"):
        raise Refusal("terminal question action step cannot publish a new artifact")
    if outcome == "continue" and report.get("answers") is not None:
        raise Refusal("continuing preparation step cannot publish question answers")
    if outcome == "continue" and report.get("next_actions") is not None:
        raise Refusal("continuing preparation step cannot select the next action")
    if outcome == "terminal" and not isinstance(report.get("answers"), dict):
        raise Refusal("terminal question action step must publish question answers")
    produced: dict[str, dict[str, Any]] = {}
    if outcome == "continue":
        raw_produced = report.get("produced_artifacts")
        if not isinstance(raw_produced, dict) or set(raw_produced) != set(definition["produces"]):
            raise Refusal("preparation step did not publish its declared artifacts")
        total_bytes = 0
        for name in definition["produces"]:
            root = artifact_root / name
            descriptor, size = _validate_artifact(
                raw_produced[name],
                root,
                action_sha256,
                step.sha256,
            )
            total_bytes += size
            produced[name] = {"root": str(root), "artifact": descriptor}
        if total_bytes > definition["output_bytes_max"]:
            raise Refusal("preparation step exceeded its approved artifact byte ceiling")
    return produced, outcome == "terminal", report.get("answers")


def _validate_artifact(
    value: object,
    root: Path,
    action_sha256: str,
    step_sha256: str,
) -> tuple[dict[str, Any], int]:
    if not isinstance(value, dict) or set(value) != {"schema", "id", "producer", "files"}:
        raise Refusal("prepared artifact descriptor has missing or unknown fields")
    if value.get("schema") != ARTIFACT_SCHEMA:
        raise Refusal("prepared artifact descriptor has an unsupported schema")
    producer = value.get("producer")
    if producer != {"action_sha256": action_sha256, "step_sha256": step_sha256}:
        raise Refusal("prepared artifact producer identity differs")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise Refusal("prepared artifact must declare at least one file")
    expected_paths: list[str] = []
    total_bytes = 0
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise Refusal("prepared artifact file identity is invalid")
        relative = _safe_relative(record.get("path"))
        byte_count = record.get("bytes")
        sha256 = record.get("sha256")
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or not isinstance(sha256, str)
            or not SHA256.fullmatch(sha256)
        ):
            raise Refusal("prepared artifact file size or hash is invalid")
        expected_paths.append(relative)
        total_bytes += byte_count
    if expected_paths != sorted(set(expected_paths)):
        raise Refusal("prepared artifact file paths must be sorted and unique")
    identity = {
        "schema": ARTIFACT_SCHEMA,
        "producer": producer,
        "files": files,
    }
    if value.get("id") != digest(canonical_json(identity)):
        raise Refusal("prepared artifact identity differs from its descriptor")
    if root.is_symlink() or not root.is_dir():
        raise Refusal("prepared artifact root is absent or unsafe")
    actual_paths: list[str] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise Refusal("prepared artifact contains a symlink")
        if path.is_file():
            actual_paths.append(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise Refusal("prepared artifact contains a non-regular entry")
    if sorted(actual_paths) != expected_paths:
        raise Refusal("prepared artifact files differ from its descriptor")
    for record in files:
        path = root / record["path"]
        if path.stat().st_size != record["bytes"] or digest(path.read_bytes()) != record["sha256"]:
            raise Refusal(f"prepared artifact file differs: {record['path']}")
    return value, total_bytes


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise Refusal("prepared artifact file path is required")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise Refusal(f"unsafe prepared artifact file path: {value!r}")
    return value


def bound_material_generations(
    prior_artifacts: dict[str, dict[str, Any]],
    consumed: list[str],
    model: str,
) -> set[str]:
    """Return generations bound by strict material records in consumed artifacts."""
    generations: set[str] = set()
    for name in consumed:
        item = prior_artifacts.get(name)
        if not isinstance(item, dict):
            continue
        root = Path(item.get("root", ""))
        descriptor = item.get("artifact")
        if not isinstance(descriptor, dict):
            continue
        records = {
            record.get("path"): record
            for record in descriptor.get("files", [])
            if isinstance(record, dict)
        }
        material_record = records.get(MATERIAL_FILE)
        if material_record is None:
            continue
        path = root / MATERIAL_FILE
        try:
            data = path.read_bytes()
            document = json.loads(data)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise Refusal("prepared material record is unreadable") from error
        if (
            not isinstance(document, dict)
            or set(document) != {
                "schema", "model", "generation", "manifest",
                "manifest_lock", "temper_binding",
            }
            or canonical_json(document) != data
            or document.get("schema") != MATERIAL_SCHEMA
            or document.get("model") != model
            or not isinstance(document.get("generation"), str)
            or not SHA256.fullmatch(document["generation"])
        ):
            raise Refusal("prepared material record is invalid")
        for field in ("manifest", "manifest_lock", "temper_binding"):
            identity = document.get(field)
            if (
                not isinstance(identity, dict)
                or set(identity) != {"path", "sha256"}
                or _safe_relative(identity.get("path")) not in records
                or not isinstance(identity.get("sha256"), str)
                or not SHA256.fullmatch(identity["sha256"])
                or records[identity["path"]].get("sha256") != identity["sha256"]
            ):
                raise Refusal(f"prepared material {field} identity is invalid")
        if material_record.get("sha256") != digest(data):
            raise Refusal("prepared material record identity differs")
        generations.add(document["generation"])
    return generations
