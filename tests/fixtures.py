from __future__ import annotations

from pathlib import Path

from fieldkit_runtime.catalog import QuestionPackage, canonical_json, digest


def question_material() -> tuple[dict, bytes, dict[str, bytes]]:
    files = {
        "fixture-protocol.py": b"# synthetic protocol fixture\n",
        "execution.lock.json": b"opaque Temper fixture lock\n",
        "PROMPT.md": b"# Synthetic question fixture\n",
    }

    def identity(path: str) -> dict[str, object]:
        return {"path": path, "sha256": digest(files[path])}

    package = {
        "schema": "field-kit-question-package/v2",
        "id": "fixture-question",
        "revision": 1,
        "origin": {"kind": "synthetic-test-fixture"},
        "host": {
            "program": "temper",
            "minimum_version": "0.1.0-alpha.4",
            "required_primitives": [
                "apply",
                "check",
                "execution-export",
                "fetch",
                "field-kit-bind",
                "machine-facts",
                "probe-serve",
                "software-check",
                "software-install",
                "software-remove",
            ],
        },
        "question": "Can the synthetic fixture complete its workflow?",
        "decision": "Whether the generic workflow mechanics remain usable.",
        "kind": "fixed",
        "summary": "Exercises question package mechanics without a model or network access.",
        "evidence_scope": "Synthetic workflow evidence; no product or machine claim.",
        "applicability": {
            "os": "darwin",
            "arch": "arm64",
            "distribution": "macos",
            "chip_prefixes": ["Apple M"],
            "min_physical_memory_mib": 16384,
            "max_physical_memory_mib": 0,
            "min_wired_limit_mib": 16384,
        },
        "relevance": {"purpose": "test-only"},
        "cost": {
            "setup_minutes_min": 0,
            "setup_minutes_max": 5,
            "live_runtime_minutes_max": 1,
            "network_bytes_max": 0,
            "temporary_disk_bytes_max": 1048576,
            "retained_disk_bytes_max": 1048576,
            "evidence_bytes_max": 2097152,
            "memory_pressure": "synthetic only",
            "service_disruption": "none",
            "paid_provider_exposure": "none",
        },
        "consent": {
            "reads": ["synthetic fixture facts"],
            "writes": ["dedicated temporary test paths"],
            "network_destinations": ["none"],
            "cleanup": "remove only the marker-owned fixture root",
        },
        "profile": {"layout": "fixture-layout"},
        "investigation": {
            "schema": "field-kit-investigation/v1",
            "parameters": [],
            "actions": [{
                "id": "measure-fixture",
                "kind": "measurement",
                "parameters": [],
                "attempts_max": 1,
                "runtime_minutes_max": 1,
                "steps": [{
                    "id": "measure-fixture",
                    "kind": "measurement",
                    "runtime_minutes_max": 1,
                    "output_bytes_max": 0,
                    "evidence_bytes_max": 1048576,
                    "consumes": [],
                    "produces": [],
                    "process_watch": None,
                }],
            }],
            "initial_action": {
                "id": "measure-fixture",
                "parameters": {},
            },
            "final_validation_action": "measure-fixture",
            "action_selection": "fixed",
            "total_attempts_max": 1,
            "total_runtime_minutes_max": 1,
        },
        "execution_lock": identity("execution.lock.json"),
        "mechanics": {
            "orchestration": "field-kit-python/v1",
            "installation": "field-kit-fixture",
            "mode": "fixture",
            "prompt": identity("PROMPT.md"),
            "runner": identity("fixture-protocol.py"),
            "protocols": [],
            "runtime_protocol": {
                "id": "fixture-protocol",
                "revision": 1,
                "schema": "field-kit-fixture-protocol/v1",
            },
        },
        "report": {
            "answer_fields": ["workflow"],
            "required_conditions": ["protocol-evidence-complete"],
            "submission": "explicit-export-only",
        },
        "invalidation_triggers": ["fixture contract changes"],
    }
    data = canonical_json(package)
    return package, data, files


def question_entry() -> QuestionPackage:
    package, data, files = question_material()
    return QuestionPackage(
        reference={
            "id": package["id"],
            "revision": package["revision"],
            "availability": "active",
            "package_path": "packages/fixture-question/package.json",
            "package_sha256": digest(data),
            "reason": "synthetic fixture",
        },
        package=package,
        package_data=data,
        package_root=Path(__file__).resolve().parent,
        files=files,
    )


def adaptive_question_entry(
    *,
    action_selection: str = "operator-bounded",
    target_minimum: int = 8192,
    target_maximum: int = 262144,
    target_step: int = 1,
    target_default: int = 98304,
) -> QuestionPackage:
    package, _, files = question_material()
    package["kind"] = "bounded-adaptive"
    package["question"] = "Which synthetic context target is usable?"
    package["decision"] = "Which witnessed synthetic target to retain."
    package["investigation"] = {
        "schema": "field-kit-investigation/v1",
        "parameters": [{
            "id": "target-context-tokens",
            "type": "integer",
            "minimum": target_minimum,
            "maximum": target_maximum,
            "step": target_step,
            "default": target_default,
        }],
        "actions": [
            {
                "id": "final-context-validation",
                "kind": "final-validation",
                "parameters": ["target-context-tokens"],
                "attempts_max": 1,
                "runtime_minutes_max": 2,
                "steps": [
                    {
                        "id": "prepare-context",
                        "kind": "preparation",
                        "runtime_minutes_max": 1,
                        "output_bytes_max": 1048576,
                        "evidence_bytes_max": 2097152,
                        "consumes": [],
                        "produces": ["prepared-context"],
                        "process_watch": None,
                    },
                    {
                        "id": "measure-context",
                        "kind": "measurement",
                        "runtime_minutes_max": 1,
                        "output_bytes_max": 0,
                        "evidence_bytes_max": 1048576,
                        "consumes": ["prepared-context"],
                        "produces": [],
                        "process_watch": None,
                    },
                ],
            },
            {
                "id": "measure-context",
                "kind": "measurement",
                "parameters": ["target-context-tokens"],
                "attempts_max": 2,
                "runtime_minutes_max": 2,
                "steps": [
                    {
                        "id": "prepare-context",
                        "kind": "preparation",
                        "runtime_minutes_max": 1,
                        "output_bytes_max": 1048576,
                        "evidence_bytes_max": 2097152,
                        "consumes": [],
                        "produces": ["prepared-context"],
                        "process_watch": None,
                    },
                    {
                        "id": "measure-context",
                        "kind": "measurement",
                        "runtime_minutes_max": 1,
                        "output_bytes_max": 0,
                        "evidence_bytes_max": 1048576,
                        "consumes": ["prepared-context"],
                        "produces": [],
                        "process_watch": None,
                    },
                ],
            },
        ],
        "initial_action": {
            "id": "measure-context",
            "parameters": {"target-context-tokens": target_default},
        },
        "final_validation_action": "final-context-validation",
        "action_selection": action_selection,
        "total_attempts_max": 3,
        "total_runtime_minutes_max": 6,
    }
    package["cost"]["live_runtime_minutes_max"] = 6
    package["cost"]["evidence_bytes_max"] = 16777216
    data = canonical_json(package)
    return QuestionPackage(
        reference={
            "id": package["id"],
            "revision": package["revision"],
            "availability": "active",
            "package_path": "packages/fixture-question/package.json",
            "package_sha256": digest(data),
            "reason": "synthetic fixture",
        },
        package=package,
        package_data=data,
        package_root=Path(__file__).resolve().parent,
        files=files,
    )


def write_question_catalog(
    root: Path,
    *,
    availability: str = "active",
    availability_reason: str = "synthetic fixture",
) -> Path:
    package, data, files = question_material()
    package_root = root / "packages" / "fixture-question"
    package_root.mkdir(parents=True)
    (package_root / "package.json").write_bytes(data)
    for relative, contents in files.items():
        (package_root / relative).write_bytes(contents)
    catalog = {
        "schema": "field-kit-question-catalog/v3",
        "revision": 1,
        "compiled_at": "2026-09-01T00:00:00Z",
        "questions": [{
            "id": package["id"],
            "revision": package["revision"],
            "availability": availability,
            "package_path": "packages/fixture-question/package.json",
            "package_sha256": digest(data),
            "reason": "synthetic fixture",
        }],
    }
    catalog_path = root / "questions.json"
    catalog_path.write_bytes(canonical_json(catalog))
    return catalog_path
