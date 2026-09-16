"""Preparation adapter for Temper-owned execution-lock compilation.

This does not create a participant session or authorize an investigation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Sequence

from .catalog import Refusal
from .workflow import CommandFailure, CommandResult, run_process_silent


HASH = re.compile(r"^[0-9a-f]{64}$")
ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
INPUTS = {"manifest.yaml", "manifest.lock.yaml", "software.lock.yaml", "request-defaults.json"}


def prepare_execution(
    temper: Path,
    lock: Path,
    destination: Path,
    *,
    dry_run: bool = False,
    runner: Callable[[Sequence[str], float], CommandResult] = run_process_silent,
) -> dict:
    """Receive exact derived inputs through the documented public CLI.

    Only Temper interprets the execution graph. Field Kit checks the returned
    identities and destination binding before exposing the prepared inputs.
    """
    lock = Path(os.path.abspath(lock.expanduser()))
    destination = Path(os.path.abspath(destination.expanduser()))
    if lock.is_symlink() or not lock.is_file() or lock.stat().st_size > 4 * 1024 * 1024:
        raise Refusal("execution lock must be a regular file no larger than 4 MiB")
    original = lock.read_bytes()
    lock_hash = hashlib.sha256(original).hexdigest()
    arguments = [str(temper), "execution", "export", "--lock", str(lock),
                 "--out", str(destination), "--json"]
    if dry_run:
        arguments.append("--dry-run")
    result = runner(arguments, 60)
    if result.returncode:
        raise CommandFailure(arguments, result)
    if len(result.stdout) > 64 * 1024:
        raise Refusal("Temper execution export exceeded the response bound")
    try:
        document = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise Refusal("Temper returned invalid execution-input JSON") from error
    if not isinstance(document, dict) or set(document) != {
        "schema", "profile", "execution_digest", "lock_sha256", "layouts", "inputs", "changed", "dry_run"
    }:
        raise Refusal("Temper returned an unknown execution-input contract")
    if document["schema"] != "temper-execution-inputs/v1":
        raise Refusal("Temper does not support the required execution-input contract")
    if document["lock_sha256"] != lock_hash or lock.read_bytes() != original:
        raise Refusal("execution lock identity changed during preparation")
    if not isinstance(document["profile"], str) or not ID.fullmatch(document["profile"]):
        raise Refusal("Temper returned an invalid selected profile")
    if not isinstance(document["execution_digest"], str) or not HASH.fullmatch(document["execution_digest"]):
        raise Refusal("Temper returned an invalid execution digest")
    layouts = document["layouts"]
    if not isinstance(layouts, list) or not layouts or any(not isinstance(i, str) or not ID.fullmatch(i) for i in layouts) or len(set(layouts)) != len(layouts):
        raise Refusal("Temper returned invalid selected layouts")
    if type(document["changed"]) is not bool or type(document["dry_run"]) is not bool or document["dry_run"] != dry_run:
        raise Refusal("Temper returned an inconsistent preparation disposition")
    if not isinstance(document["inputs"], dict) or set(document["inputs"]) != INPUTS:
        raise Refusal("Temper returned an incomplete execution input set")
    for name, identity in document["inputs"].items():
        path = destination / name
        if not isinstance(identity, dict) or set(identity) != {"path", "sha256"} or identity["path"] != str(path):
            raise Refusal("Temper returned an input outside the requested destination")
        if not isinstance(identity["sha256"], str) or not HASH.fullmatch(identity["sha256"]):
            raise Refusal("Temper returned an invalid input hash")
        if not dry_run:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                raise Refusal("prepared execution input is absent, unsafe or too large")
            if hashlib.sha256(path.read_bytes()).hexdigest() != identity["sha256"]:
                raise Refusal("prepared execution input differs from Temper's export")
    return document
