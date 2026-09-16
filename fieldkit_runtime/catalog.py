"""Pure loading, validation, applicability, and disclosure for Field Kit."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


CATALOG_SCHEMA = "field-kit-question-catalog/v3"
PACKAGE_SCHEMA = "field-kit-question-package/v2"
PROMOTION_SCHEMA = "field-kit-catalog-promotion/v1"
ORCHESTRATION = "field-kit-python/v1"
MACHINE_SCHEMA = "temper-machine-facts/v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTITY = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ACTIVE = "active"
QUALIFYING = "qualifying"
SUSPENDED = "suspended"
AVAILABILITIES = frozenset({ACTIVE, QUALIFYING, SUSPENDED})
CHECK_CLASSES = frozenset({
    "structural", "integrity", "interface", "operational", "behavioral", "performance",
})
CHECK_RESULTS = frozenset({"pass", "fail", "unknown"})
PROMOTION_TRANSITIONS = frozenset({
    ("absent", QUALIFYING),
    (QUALIFYING, ACTIVE),
    (SUSPENDED, ACTIVE),
    (ACTIVE, SUSPENDED),
})


class Refusal(ValueError):
    """A reviewed input or machine does not satisfy the Field Kit contract."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=False) + "\n").encode()


def _read_regular(path: Path, limit: int = 1024 * 1024) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"expected a regular file without symlink indirection: {path}")
    size = path.stat().st_size
    if size > limit:
        raise Refusal(f"file exceeds {limit} bytes: {path}")
    return path.read_bytes()


def _load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    data = _read_regular(path)
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict):
        raise Refusal(f"expected a JSON object: {path}")
    if canonical_json(document) != data:
        raise Refusal(f"JSON is not canonical Field Kit formatting: {path}")
    return document, data


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise Refusal("referenced path is required")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise Refusal(f"unsafe referenced path: {value!r}")
    return value


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise Refusal(f"{label} must be a non-empty string list")
    if value != sorted(set(value)):
        raise Refusal(f"{label} must be sorted and unique")
    return value


def _line(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\n" in value or "\r" in value:
        raise Refusal(f"{label} must be one non-empty trimmed line")
    return value


def _utc_timestamp(value: object, label: str) -> str:
    timestamp = _line(value, label)
    if not UTC_TIMESTAMP.fullmatch(timestamp):
        raise Refusal(f"{label} must be an RFC 3339 UTC timestamp with whole seconds")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise Refusal(f"{label} is not a valid UTC timestamp") from error
    return timestamp




@dataclass(frozen=True)
class MachineFacts:
    document: dict[str, Any]

    @property
    def target(self) -> dict[str, str]:
        return self.document["target"]

    @property
    def physical_memory_mib(self) -> int:
        return int(self.document["physical_memory_bytes"]) // (1024 * 1024)


@dataclass(frozen=True)
class QuestionMaterial:
    package: dict[str, Any]
    package_data: bytes
    package_root: Path
    files: dict[str, bytes]

    @property
    def selector(self) -> str:
        return f"{self.package['id']}@{self.package['revision']}"

    @property
    def package_sha256(self) -> str:
        return digest(self.package_data)


def load_question_material(path: Path) -> QuestionMaterial:
    package_path = path.expanduser().absolute()
    package, package_data = _load_json(package_path)
    files = _validate_package(package, package_path.parent)
    return QuestionMaterial(package, package_data, package_path.parent, files)


def parse_machine_facts(data: bytes) -> MachineFacts:
    """Parse Temper's intentionally small canonical facts YAML without PyYAML."""
    document: dict[str, Any] = {}
    target: dict[str, str] = {}
    section = ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Refusal("machine facts are not UTF-8") from error
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        if raw == "target:":
            section = "target"
            continue
        if raw.startswith("  ") and section == "target":
            key, separator, value = raw.strip().partition(": ")
            if not separator or not key or not value:
                raise Refusal("machine facts contain an invalid target field")
            target[key] = value
            continue
        section = ""
        key, separator, value = raw.partition(": ")
        if not separator or not key or not value:
            raise Refusal("machine facts contain an invalid field")
        if key in {"physical_memory_bytes", "metal_device_memory_mib", "wired_limit_mib"}:
            try:
                document[key] = int(value)
            except ValueError as error:
                raise Refusal(f"machine fact {key} must be an integer") from error
        else:
            document[key] = value
    document["target"] = target
    required = {
        "schema", "target", "hardware_model", "chip", "os_build",
        "physical_memory_bytes", "metal_device_memory_mib", "wired_limit_mib",
    }
    if not required.issubset(document) or document.get("schema") != MACHINE_SCHEMA:
        raise Refusal("machine facts are incomplete or use an unsupported schema")
    if not {"os", "arch", "distribution"}.issubset(target):
        raise Refusal("machine target is incomplete")
    return MachineFacts(document)


@dataclass(frozen=True)
class QuestionPackage:
    reference: dict[str, Any]
    package: dict[str, Any]
    package_data: bytes
    package_root: Path
    files: dict[str, bytes]

    @property
    def selector(self) -> str:
        return f"{self.package['id']}@{self.package['revision']}"

    @property
    def package_sha256(self) -> str:
        return digest(self.package_data)

    @property
    def availability_reason(self) -> str:
        return self.reference["reason"]

    def availability_notice(self) -> str:
        availability = self.reference["availability"]
        reason = self.availability_reason
        if availability == QUALIFYING:
            return "Qualification only: " + reason
        if availability == SUSPENDED:
            return "New participant starts suspended: " + reason
        return ""

    def availability_disclosure(self) -> str:
        """Describe package status without reading facts from the participant's machine."""
        return "\n".join(
            [
                f"FIELD KIT — {self.package['question']}",
                f"Question package: {self.selector}",
                f"Availability: {self.reference['availability']}",
                "",
                self.package["summary"],
                "",
                self.availability_notice(),
            ]
        ) + "\n"

    def applicable(self, facts: MachineFacts) -> tuple[bool, list[str]]:
        rule = self.package["applicability"]
        target = facts.target
        reasons: list[str] = []
        for key in ("os", "arch", "distribution"):
            if rule[key] != target[key]:
                reasons.append(f"requires {key}={rule[key]}, found {target[key]}")
        prefixes = rule.get("chip_prefixes", [])
        if prefixes and not any(str(facts.document["chip"]).startswith(prefix) for prefix in prefixes):
            reasons.append(f"requires chip prefix {', '.join(prefixes)}, found {facts.document['chip']}")
        physical = facts.physical_memory_mib
        exact_physical = int(rule.get("physical_memory_bytes", 0))
        if exact_physical and int(facts.document["physical_memory_bytes"]) != exact_physical:
            reasons.append(
                f"requires exactly {exact_physical} bytes physical memory, "
                f"found {facts.document['physical_memory_bytes']}"
            )
        if physical < int(rule["min_physical_memory_mib"]):
            reasons.append(f"requires at least {rule['min_physical_memory_mib']} MiB physical memory, found {physical}")
        maximum = int(rule.get("max_physical_memory_mib", 0))
        if maximum and physical > maximum:
            reasons.append(f"requires at most {maximum} MiB physical memory, found {physical}")
        wired = int(facts.document["wired_limit_mib"])
        if wired < int(rule["min_wired_limit_mib"]):
            reasons.append(f"requires at least {rule['min_wired_limit_mib']} MiB wired limit, found {wired}")
        return not reasons, reasons

    def disclosure(self, facts: MachineFacts) -> str:
        package = self.package
        cost = package["cost"]
        consent = package["consent"]
        applicable, reasons = self.applicable(facts)
        lines = [
            f"FIELD KIT — {package['question']}",
            f"Question package: {self.selector}",
            f"Availability: {self.reference['availability']}",
            f"Applicable: {'yes' if applicable else 'no'}",
            "",
            package["summary"],
            "",
            "Evidence boundary:",
            package["evidence_scope"],
            "",
            "Maximum declared cost:",
            f"- setup: {cost['setup_minutes_min']}–{cost['setup_minutes_max']} minutes",
            f"- all question actions: {cost['live_runtime_minutes_max']} minutes",
            f"- network: {cost['network_bytes_max']} bytes",
            f"- temporary disk: {cost['temporary_disk_bytes_max']} bytes",
            f"- retained disk: {cost['retained_disk_bytes_max']} bytes",
            f"- local evidence and report: {cost['evidence_bytes_max']} bytes",
            f"- memory pressure: {cost['memory_pressure']}",
            f"- service disruption: {cost['service_disruption']}",
            f"- paid providers: {cost['paid_provider_exposure']}",
            "",
            "Reads: " + "; ".join(consent["reads"]),
            "Writes: " + "; ".join(consent["writes"]),
            "Network: " + "; ".join(consent["network_destinations"]),
            "Cleanup: " + consent["cleanup"],
            "Output: local only; nothing is uploaded.",
        ]
        if self.reference["availability"] != ACTIVE:
            lines.extend(["", self.availability_notice()])
        if reasons:
            lines.extend(["", "Applicability refusals:"] + [f"- {reason}" for reason in reasons])
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class QuestionCatalog:
    path: Path
    document: dict[str, Any]
    data: bytes
    questions: tuple[QuestionPackage, ...]

    @classmethod
    def load(cls, path: Path) -> "QuestionCatalog":
        path = path.resolve()
        document, data = _load_json(path)
        if set(document) != {"schema", "revision", "compiled_at", "questions"}:
            raise Refusal("question catalog has missing or unknown fields")
        if (
            document.get("schema") != CATALOG_SCHEMA
            or isinstance(document.get("revision"), bool)
            or not isinstance(document.get("revision"), int)
            or document["revision"] <= 0
        ):
            raise Refusal("unsupported question catalog schema or revision")
        _utc_timestamp(document.get("compiled_at"), "question catalog compiled_at")
        references = document.get("questions")
        if not isinstance(references, list):
            raise Refusal("question catalog entries must be a list")
        questions: list[QuestionPackage] = []
        previous: tuple[str, int] | None = None
        active: set[str] = set()
        for reference in references:
            if not isinstance(reference, dict):
                raise Refusal("question reference must be an object")
            if set(reference) != {"id", "revision", "availability", "package_path", "package_sha256", "reason"}:
                raise Refusal("question reference has missing or unknown fields")
            identity = f"{reference.get('id')}@{reference.get('revision')}"
            if (
                not IDENTITY.fullmatch(str(reference.get("id", "")))
                or isinstance(reference.get("revision"), bool)
                or not isinstance(reference.get("revision"), int)
                or reference["revision"] <= 0
            ):
                raise Refusal(f"invalid question reference identity {identity}")
            if not isinstance(reference.get("package_sha256"), str) or not SHA256.fullmatch(reference["package_sha256"]):
                raise Refusal(f"question reference {identity} has an invalid package hash")
            sort_key = (reference["id"], reference["revision"])
            if previous is not None and sort_key <= previous:
                raise Refusal("question references must be uniquely sorted")
            previous = sort_key
            availability = reference.get("availability")
            if availability not in AVAILABILITIES:
                raise Refusal(f"unsupported availability for {identity}")
            _line(reference.get("reason"), f"question {identity} availability reason")
            if availability == ACTIVE:
                if str(reference.get("id")) in active:
                    raise Refusal(f"multiple active revisions for {reference.get('id')}")
                active.add(str(reference.get("id")))
            package_path = path.parent / _safe_relative(reference.get("package_path"))
            material = load_question_material(package_path)
            if material.package_sha256 != reference.get("package_sha256"):
                raise Refusal(f"package hash mismatch for {identity}")
            if material.package.get("id") != reference.get("id") or material.package.get("revision") != reference.get("revision"):
                raise Refusal(f"package identity mismatch for {identity}")
            questions.append(QuestionPackage(
                reference,
                material.package,
                material.package_data,
                material.package_root,
                material.files,
            ))
        return cls(path, document, data, tuple(questions))

    def active(self) -> tuple[QuestionPackage, ...]:
        return tuple(entry for entry in self.questions if entry.reference["availability"] == ACTIVE)

    def qualifying(self) -> tuple[QuestionPackage, ...]:
        return tuple(entry for entry in self.questions if entry.reference["availability"] == QUALIFYING)

    def find(self, selector: str) -> QuestionPackage:
        for entry in self.questions:
            if entry.selector == selector:
                return entry
        raise Refusal(f"unknown Field Kit question {selector!r}")


def _validate_package(package: dict[str, Any], root: Path) -> dict[str, bytes]:
    from .actions import validate_investigation
    from .answers import validate_answer_fields

    identity = f"{package.get('id')}@{package.get('revision')}"
    if not IDENTITY.fullmatch(str(package.get("id", ""))) or not isinstance(package.get("revision"), int) or package["revision"] <= 0:
        raise Refusal(f"invalid package identity {identity}")
    if package.get("schema") != PACKAGE_SCHEMA:
        raise Refusal(f"package {identity} has an unsupported schema")
    if package.get("mechanics", {}).get("orchestration") != ORCHESTRATION:
        raise Refusal(f"package {identity} is not owned by the Field Kit Python runtime")
    required_fields = {
        "schema", "id", "revision", "origin", "host", "question", "decision", "kind", "summary",
        "evidence_scope", "applicability", "relevance", "cost", "consent",
        "profile", "execution_lock", "investigation", "mechanics", "report",
        "invalidation_triggers",
    }
    if set(package) != required_fields:
        raise Refusal(f"package {identity} has missing or unknown fields")
    for key in ("question", "decision", "summary", "evidence_scope"):
        if not isinstance(package.get(key), str) or not package[key].strip():
            raise Refusal(f"package {identity} requires {key}")
    if package.get("kind") not in {"fixed", "bounded-adaptive"}:
        raise Refusal(f"package {identity} has an invalid question kind")
    applicability = package.get("applicability")
    required_applicability = {
        "os", "arch", "distribution", "chip_prefixes",
        "min_physical_memory_mib", "max_physical_memory_mib",
        "min_wired_limit_mib",
    }
    if (
        not isinstance(applicability, dict)
        or not required_applicability.issubset(applicability)
        or set(applicability) - (required_applicability | {"physical_memory_bytes"})
    ):
        raise Refusal(f"package {identity} applicability has missing or unknown fields")
    for key in ("os", "arch", "distribution"):
        _line(applicability.get(key), f"applicability.{key}")
    _strings(applicability.get("chip_prefixes"), "applicability.chip_prefixes")
    for key in (
        "min_physical_memory_mib", "max_physical_memory_mib", "min_wired_limit_mib",
    ):
        value = applicability.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise Refusal(f"package {identity} applicability.{key} must be a non-negative integer")
    exact_physical = applicability.get("physical_memory_bytes")
    if exact_physical is not None and (
        isinstance(exact_physical, bool)
        or not isinstance(exact_physical, int)
        or exact_physical <= 0
    ):
        raise Refusal(f"package {identity} applicability.physical_memory_bytes must be a positive integer")
    investigation = validate_investigation(package.get("investigation"), package["kind"])
    cost = package.get("cost")
    cost_fields = {
        "setup_minutes_min", "setup_minutes_max", "live_runtime_minutes_max",
        "network_bytes_max", "temporary_disk_bytes_max", "retained_disk_bytes_max",
        "evidence_bytes_max",
        "memory_pressure", "service_disruption", "paid_provider_exposure",
    }
    if not isinstance(cost, dict) or set(cost) != cost_fields:
        raise Refusal(f"package {identity} cost has missing or unknown fields")
    numeric_costs = (
        "setup_minutes_min", "setup_minutes_max", "live_runtime_minutes_max",
        "network_bytes_max", "temporary_disk_bytes_max", "retained_disk_bytes_max",
        "evidence_bytes_max",
    )
    if any(isinstance(cost[field], bool) or not isinstance(cost[field], int) or cost[field] < 0 for field in numeric_costs):
        raise Refusal(f"package {identity} cost ceilings must be non-negative integers")
    if cost["setup_minutes_min"] > cost["setup_minutes_max"]:
        raise Refusal(f"package {identity} setup time range is inconsistent")
    if any(not isinstance(cost[field], str) or not cost[field].strip() for field in (
        "memory_pressure", "service_disruption", "paid_provider_exposure",
    )):
        raise Refusal(f"package {identity} cost descriptions must be non-empty strings")
    if cost["live_runtime_minutes_max"] != investigation["total_runtime_minutes_max"]:
        raise Refusal(f"package {identity} cost and investigation runtime ceilings differ")
    possible_action_evidence: list[int] = []
    for action in investigation["actions"]:
        per_attempt = sum(step["evidence_bytes_max"] for step in action["steps"])
        possible_action_evidence.extend([per_attempt] * action["attempts_max"])
    maximum_action_evidence = sum(sorted(
        possible_action_evidence,
        reverse=True,
    )[:investigation["total_attempts_max"]])
    if maximum_action_evidence > cost["evidence_bytes_max"]:
        raise Refusal(
            f"package {identity} action evidence can exceed its evidence byte ceiling"
        )
    host = package.get("host")
    if not isinstance(host, dict) or host.get("program") != "temper" or not isinstance(host.get("minimum_version"), str):
        raise Refusal(f"package {identity} has no Temper host compatibility declaration")
    required_primitives = _strings(host.get("required_primitives"), "host.required_primitives")
    baseline_primitives = [
        "apply", "check", "fetch", "field-kit-bind", "machine-facts",
        "probe-serve", "software-check", "software-install", "software-remove",
    ]
    recognized_primitives = set(baseline_primitives) | {"probe-tokenize", "execution-export", "catalog-compile"}
    if (
        not (set(baseline_primitives) | {"execution-export"}).issubset(required_primitives)
        or any(item not in recognized_primitives for item in required_primitives)
    ):
        raise Refusal(f"package {identity} does not declare the complete stable Temper host")
    consent = package.get("consent")
    if not isinstance(consent, dict):
        raise Refusal(f"package {identity} has no consent disclosure")
    if "allowed_outcomes" in consent:
        outcomes = consent["allowed_outcomes"]
        if (not isinstance(outcomes, list) or not outcomes
                or any(not isinstance(item, str) or item not in {"keep", "restore"} for item in outcomes)
                or outcomes != sorted(set(outcomes))):
            raise Refusal(f"package {identity} has invalid allowed outcomes")
    mechanics = package.get("mechanics")
    if not isinstance(mechanics, dict):
        raise Refusal(f"package {identity} has no mechanics")
    protocol = mechanics.get("runtime_protocol")
    if (
        not isinstance(protocol, dict)
        or not IDENTITY.fullmatch(str(protocol.get("id", "")))
        or not isinstance(protocol.get("revision"), int)
        or protocol["revision"] <= 0
        or not isinstance(protocol.get("schema"), str)
        or not protocol["schema"].startswith("field-kit-")
    ):
        raise Refusal(f"package {identity} has an invalid Field Kit protocol identity")
    lock = package.get("execution_lock")
    if not isinstance(lock, dict) or set(lock) != {"path", "sha256"}:
        raise Refusal(f"package {identity} requires one exact execution lock")
    identities: list[dict[str, Any]] = [lock]
    if set(mechanics) != {"orchestration", "installation", "mode", "prompt", "runner", "protocols", "runtime_protocol"}:
        raise Refusal(f"package {identity} mechanics has missing or unknown fields")
    for key in ("installation", "mode"):
        if not IDENTITY.fullmatch(str(mechanics.get(key, ""))):
            raise Refusal(f"package {identity} has invalid mechanics.{key}")
    if not isinstance(package.get("profile"), dict) or set(package["profile"]) != {"layout"} or not IDENTITY.fullmatch(str(package["profile"]["layout"])):
        raise Refusal(f"package {identity} requires one selected layout")
    for key in ("prompt", "runner"):
        item = mechanics.get(key)
        if not isinstance(item, dict):
            raise Refusal(f"package {identity} is missing mechanics.{key}")
        identities.append(item)
    protocols = mechanics.get("protocols", [])
    if not isinstance(protocols, list):
        raise Refusal(f"package {identity} protocols must be a list")
    identities.extend(protocols)
    files: dict[str, bytes] = {}
    for item in identities:
        relative = _safe_relative(item.get("path"))
        expected = item.get("sha256")
        if (
            relative == "package.json"
            or not isinstance(expected, str)
            or not SHA256.fullmatch(expected)
            or relative in files
        ):
            raise Refusal(f"package {identity} has an invalid file identity")
        data = _read_regular(root / relative)
        if digest(data) != expected:
            raise Refusal(f"package {identity} file hash mismatch: {relative}")
        files[relative] = data
    _strings(package.get("report", {}).get("required_conditions"), "report.required_conditions")
    validate_answer_fields(package.get("report", {}).get("answer_fields"))
    if package.get("report", {}).get("submission") != "explicit-export-only":
        raise Refusal(f"package {identity} has an invalid submission policy")
    return files
