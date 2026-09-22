"""Read Temper's execution identity without coordinating legacy export files."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .catalog import IDENTITY, SHA256, Refusal, digest
from .workflow import CommandFailure, run_process_silent


def inspect_execution(temper: Path, lock: Path, *, runner=run_process_silent) -> dict:
    lock = Path(os.path.abspath(lock.expanduser()))
    if lock.is_symlink() or not lock.is_file() or lock.stat().st_size > 4 * 1024 * 1024:
        raise Refusal("execution lock must be a regular file no larger than 4 MiB")
    original = lock.read_bytes()
    arguments = [str(temper), "execution", "inspect", "--lock", str(lock)]
    result = runner(arguments, 60)
    if result.returncode:
        raise Refusal("Temper could not inspect this execution lock. Run ./setup.sh to install the required host; if using --temper, check that executable. " + str(CommandFailure(arguments, result)))
    if len(result.stdout) > 64 * 1024:
        raise Refusal("Temper execution inspection exceeded the response bound")
    try:
        document = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise Refusal("Temper returned invalid execution JSON") from error
    validate_execution(document)
    if document["lock_sha256"] != digest(original) or lock.read_bytes() != original:
        raise Refusal("execution lock identity changed during inspection")
    return document


def validate_execution(document):
    if not isinstance(document, dict) or set(document) != {
        "schema", "profile", "execution_digest", "lock_sha256", "layouts", "request_defaults"
    } or document["schema"] != "temper-execution/v1":
        raise Refusal("Temper returned an unknown execution contract")
    for key in ("execution_digest", "lock_sha256"):
        if not isinstance(document[key], str) or not SHA256.fullmatch(document[key]):
            raise Refusal("Temper returned an invalid execution identity")
    if not isinstance(document["profile"], str) or not IDENTITY.fullmatch(document["profile"]):
        raise Refusal("Temper returned an invalid selected profile")
    layouts = document["layouts"]
    if not isinstance(layouts, list) or not layouts or any(not isinstance(i, str) or not IDENTITY.fullmatch(i) for i in layouts) or layouts != sorted(set(layouts)):
        raise Refusal("Temper returned invalid selected layouts")
    if not isinstance(document["request_defaults"], dict) or sorted(document["request_defaults"]) != layouts:
        raise Refusal("Temper returned inconsistent request defaults")


def validate_material(data: bytes, execution: dict) -> dict:
    if len(data) > 1024 * 1024:
        raise Refusal("Temper material response exceeds 1 MiB")
    try:
        material = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise Refusal("Temper returned invalid material JSON") from error
    if not isinstance(material, dict) or set(material) != {"schema", "execution", "generation", "binding"} or material["schema"] != "temper-execution-material/v1":
        raise Refusal("Temper returned an unknown material contract")
    if material["execution"] != execution:
        raise Refusal("Temper prepared a different execution lock")
    if not isinstance(material["generation"], str) or not SHA256.fullmatch(material["generation"]):
        raise Refusal("Temper returned an invalid generation")
    if not isinstance(material["binding"], str) or not material["binding"].startswith("schema: temper-field-kit-binding/v1\n"):
        raise Refusal("Temper returned no material binding")
    return material
