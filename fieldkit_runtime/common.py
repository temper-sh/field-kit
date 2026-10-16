"""Small helpers shared by every Field Kit module."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTITY = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


class Refusal(ValueError):
    """A reviewed input or machine does not satisfy the Field Kit contract."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=False) + "\n").encode()


def read_regular(path: Path, limit: int = 1024 * 1024) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"expected a regular file without symlink indirection: {path}")
    if path.stat().st_size > limit:
        raise Refusal(f"file exceeds {limit} bytes: {path}")
    return path.read_bytes()


def load_json(path: Path, limit: int = 1024 * 1024) -> dict[str, Any]:
    data = read_regular(path, limit)
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict):
        raise Refusal(f"expected a JSON object: {path}")
    return document
