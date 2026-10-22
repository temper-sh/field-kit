"""The one reader of Temper execution-lock fields that Field Kit uses.

Temper owns the lock format and compiles locks; Field Kit never constructs one.
Everything else asks this module instead of walking lock records itself.
"""
from __future__ import annotations
import copy
import re
from typing import Any
from .common import canonical_json, digest

REPOSITORY = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

def preset(lock: dict[str, Any]) -> dict[str, Any]:
    return lock["records"]["presets"][lock["preset"]]

def interface(lock: dict[str, Any]) -> str | None:
    return preset(lock).get("interface")

def engine_adapter(lock: dict[str, Any]) -> str:
    return lock["records"]["engines"][preset(lock)["engine"]]["adapter"]

def installation(lock: dict[str, Any]) -> str:
    """One Temper installation per exact software closure in the study root.
    Candidates sharing an engine release and router reuse installed software."""
    records = lock["records"]
    engine = copy.deepcopy(records["engines"][preset(lock)["engine"]])
    engine.pop("presentation", None)
    return "field-kit-" + digest(canonical_json({"engine": engine, "runtime": records.get("runtime")}))[:16]

def describe(lock: dict[str, Any]) -> dict[str, Any]:
    """The reviewable facts of one candidate for an approval summary."""
    selected = preset(lock)
    return {"profiles": selected["profiles"], "request_defaults": selected.get("request_defaults"),
            "speculation": selected.get("speculation"), "artifacts": list(lock["records"].get("artifacts", {})),
            "templates": list(lock["records"].get("templates", {}))}

def frozen_material(lock: dict[str, Any]) -> dict[str, Any]:
    """Exclude only the tuning controls explicitly covered by study consent.
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

def hf_files(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Exact Hugging Face files of one lock (weights, draft, projector, template), by SHA-256."""
    files = {}
    for group in ("artifacts", "templates"):
        for record in lock["records"].get(group, {}).values():
            source = next((row for row in record.get("sources", []) if row.get("hub") == "huggingface"), None)
            for file in record.get("files", []) if source else []:
                entry = {"repo": source.get("repo"), "revision": source.get("revision"), "path": file.get("path"),
                         "bytes": file.get("bytes"), "sha256": file.get("sha256")}
                if valid_file(entry):
                    files[entry["sha256"]] = entry
    return files

def valid_file(entry: dict[str, Any]) -> bool:
    """Temper's cache-entry rules; anything else is never touched on disk."""
    path = entry["path"]
    return (isinstance(entry["repo"], str) and bool(REPOSITORY.fullmatch(entry["repo"])) and "--" not in entry["repo"]
            and isinstance(entry["revision"], str) and bool(REVISION.fullmatch(entry["revision"]))
            and isinstance(entry["sha256"], str) and bool(SHA256.fullmatch(entry["sha256"]))
            and type(entry["bytes"]) is int and entry["bytes"] >= 0 and isinstance(path, str) and bool(path)
            and not path.startswith("/") and ".." not in path.split("/") and "\\" not in path and "\x00" not in path)

def declared_material_bytes(locks: list[dict[str, Any]]) -> int:
    """Known model/template/software file sizes, deduplicated by content identity.
    This is a disclosure floor, never a runtime-memory or total-download estimate.
    """
    files = {}
    def visit(value):
        if isinstance(value, dict):
            sha = value.get("sha256")
            size = value.get("bytes", value.get("size"))
            if isinstance(sha, str) and type(size) is int and size >= 0:
                files[sha] = max(files.get(sha, 0), size)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for lock in locks:
        visit(lock["records"])
    return sum(files.values())

def software_bytes(locks: list[dict[str, Any]]) -> int:
    """Known non-weights bytes (engine releases, Python closures) for the study root."""
    weights = {}
    for lock in locks:
        weights.update(hf_files(lock))
    return max(0, declared_material_bytes(locks) - sum(entry["bytes"] for entry in weights.values()))

def context_windows(lock: dict[str, Any]) -> list[int]:
    return [profile["context_window_tokens"] for profile in preset(lock)["profiles"]]

def max_output(lock: dict[str, Any]) -> int:
    return preset(lock).get("request_defaults", {}).get("max_output_tokens", 0)
