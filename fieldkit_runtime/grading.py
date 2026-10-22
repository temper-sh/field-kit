"""The coding harness's grading environment: Field Kit's own Python plus exact test packages.

Flask's tests need pytest and Flask's dependencies. They are installed once into
.local/grading from hash-pinned wheels, never into Field Kit's own Python.
Candidate code runs only inside the harness's sandbox.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import sys
from .common import Refusal, digest
from .process import CommandFailure, atomic_write, capture_process, run_process_progress

REQUIREMENTS = Path("harnesses/coding/grading-requirements.txt")
MARKER = "field-kit-grading.json"
SCHEMA = "field-kit-grading/v1"

def python_identity():
    return {"executable": str(Path(sys.executable).resolve()), "version": sys.version.split()[0]}

def describe(repository: Path) -> dict:
    target, requirements = repository / ".local/grading", repository / REQUIREMENTS
    expected = {"requirements_sha256": digest(requirements.read_bytes()), "python": python_identity()}
    try:
        marker = json.loads((target / MARKER).read_text())
    except (OSError, ValueError):
        marker = None
    ready = bool(marker) and marker.get("schema") == SCHEMA and (target / "bin/python").is_file() \
        and all(marker.get(key) == value for key, value in expected.items())
    return {"python": str(target / "bin/python"), "ready": ready,
            "packages": marker.get("packages") if ready else None,
            "next": None if ready else "./field-kit grading installs the Flask test packages (about 4 MB, once)"}

def prepare(repository: Path, *, run=run_process_progress, capture=capture_process) -> dict:
    """Build the environment beside its final place, then move it in; a stale one is replaced."""
    state = describe(repository)
    if state["ready"]:
        return state
    target = repository / ".local/grading"
    if target.exists() and not (target / MARKER).is_file():
        raise Refusal(f"{target} exists but was not made by Field Kit; move it aside first")
    staging = target.with_name("grading.partial")
    if staging.exists():
        shutil.rmtree(staging)
    python = staging / "bin/python"
    print("field-kit: installing the Flask test packages for the coding harness (about 4 MB, once) ...",
          file=sys.stderr)
    for argv in ([sys.executable, "-m", "venv", str(staging)],
                 [str(python), "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "--no-deps",
                  "--no-input", "--disable-pip-version-check", "--no-cache-dir",
                  "-r", str(repository / REQUIREMENTS)]):
        result = run(argv, 900)
        if result.returncode:
            raise CommandFailure(argv, result)
    listed = capture([str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], 60)
    if listed.returncode:
        raise CommandFailure(["pip"], listed)
    packages = {row["name"]: row["version"] for row in json.loads(listed.stdout) if row["name"] != "pip"}
    atomic_write(staging / MARKER, json.dumps({
        "schema": SCHEMA, "requirements_sha256": digest((repository / REQUIREMENTS).read_bytes()),
        "python": python_identity(), "packages": packages}, indent=2).encode() + b"\n", 0o644)
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    return describe(repository)
