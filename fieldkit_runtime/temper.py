"""Temper's public commands, read through their --json documents.

Field Kit passes execution locks to Temper unchanged and reads only the
fields named here. Temper owns compilation, installation and serving.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from .common import SHA256, Refusal
from .process import CommandFailure, Runner, file_digest, run_process_progress, run_process_silent

MINIMUM_VERSION = (0, 3, 1)
RESPONSE_LIMIT = 16 * 1024 * 1024
VERSION = re.compile(r"temper (\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?")


def locate(explicit: Path | None = None) -> Path:
    """The Temper to use: --temper, else `temper` on PATH. A link, such as
    Homebrew's, resolves to its file, so a run records the binary itself."""
    if explicit is None:
        found = shutil.which("temper")
        if found is None:
            raise Refusal("Field Kit needs Temper 0.3.1 or newer. Install it with: brew install temper-sh/temper/temper")
        explicit = Path(found)
    return explicit.resolve()


def version_at_least(text: str, minimum: tuple[int, int, int] = MINIMUM_VERSION) -> bool:
    match = VERSION.fullmatch(text.strip())
    if not match:
        raise Refusal(f"Temper printed an unknown version: {text.strip()!r}")
    numbers = tuple(int(match.group(index)) for index in range(1, 4))
    if numbers == (0, 0, 0) and match.group(4) == "-dev":
        return True  # a local development build
    if match.group(4):
        return numbers > minimum  # 0.3.1-alpha.1 precedes 0.3.1
    return numbers >= minimum


class Temper:
    def __init__(self, path: Path, *, runner: Runner = run_process_silent, progress_runner: Runner = run_process_progress):
        self.path = Path(path)
        self.runner, self.progress_runner = runner, progress_runner

    def run(self, arguments: list[str], timeout: float, *, environment: Mapping[str, str] | None = None,
            progress: bool = False) -> bytes:
        argv = [str(self.path), *map(str, arguments)]
        runner = self.progress_runner if progress else self.runner
        if environment is None:
            result = runner(argv, timeout)
        else:
            result = runner(argv, timeout, environment=dict(environment))
        if result.returncode:
            raise CommandFailure(argv, result)
        if len(result.stdout) > RESPONSE_LIMIT:
            raise Refusal("Temper printed more than 16 MiB")
        return result.stdout

    def document(self, arguments: list[str], timeout: float, schema: str | None = None, *,
                 environment: Mapping[str, str] | None = None, progress: bool = False) -> dict[str, Any]:
        output = self.run([*arguments, "--json"], timeout, environment=environment, progress=progress)
        try:
            document = json.loads(output)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Refusal(f"Temper printed invalid JSON for {arguments[0]} {arguments[1]}") from error
        if not isinstance(document, dict) or (schema and document.get("schema") != schema):
            raise Refusal(f"Temper printed an unexpected document for {arguments[0]} {arguments[1]}")
        return document

    def identity(self) -> dict[str, str]:
        if self.path.is_symlink() or not self.path.is_file():
            raise Refusal(f"Temper must be a regular executable file: {self.path}")
        text = self.run(["version"], 30).decode(errors="replace").strip()
        if not version_at_least(text):
            raise Refusal(f"Field Kit needs Temper 0.3.1 or newer; found {text}."
                          " Upgrade it with: brew upgrade temper-sh/temper/temper")
        return {"path": str(self.path), "version": text, "sha256": file_digest(self.path)}

    def machine_facts(self) -> dict[str, Any]:
        facts = self.document(["machine", "facts"], 60, "temper-machine-facts/v1")
        for key in ("physical_memory_bytes", "wired_limit_mib"):
            if type(facts.get(key)) is not int or facts[key] <= 0:
                raise Refusal(f"Temper machine facts have no positive {key}")
        return facts

    def catalog_update(self, root: Path) -> dict[str, Any]:
        result = self.document(["catalog", "update", "--root", root], 120)
        _snapshot(result.get("active"))
        return result

    def catalog(self, root: Path) -> dict[str, Any]:
        result = self.document(["catalog", "inspect", "--root", root], 60)
        _snapshot(result.get("active"))
        catalog = result.get("catalog")
        if not isinstance(catalog, dict) or catalog.get("schema") != "temper-catalog/v1" \
                or not isinstance(catalog.get("presets"), dict) or not isinstance(catalog.get("engines"), dict):
            raise Refusal("Temper returned an unknown catalog")
        return result

    def compile(self, root: Path, preset: str, memory_gib: int, out: Path) -> dict[str, Any]:
        result = self.document(["catalog", "compile", "--root", root, "--preset", preset, "--target", "darwin/arm64",
                                "--memory-gib", str(memory_gib), "--out", out], 120, "temper-catalog-compilation/v1")
        if result.get("preset") != preset:
            raise Refusal("Temper compiled a different preset")
        return result

    def preset_check(self, root: Path, preset: str, memory_gib: int) -> dict[str, Any]:
        """Temper's check of one user preset file in the root's presets.d.

        Temper exits 1 for an invalid file and still prints its reason, which
        becomes the refusal.
        """
        argv = [str(self.path), "preset", "check", preset, "--root", str(root), "--memory-gib", str(memory_gib), "--json"]
        result = self.runner(argv, 60)
        try:
            document = json.loads(result.stdout) if len(result.stdout) <= RESPONSE_LIMIT else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
        rows = document.get("presets") if isinstance(document, dict) and document.get("schema") == "temper-preset-check/v1" else None
        checked = next((row for row in rows or [] if isinstance(row, dict) and row.get("id") == preset), None)
        if checked is None:
            if result.returncode:
                raise CommandFailure(argv, result)
            raise Refusal("Temper printed an unexpected document for preset check")
        if checked.get("valid") is not True:
            raise Refusal(f"Temper refuses the preset file: {checked.get('error') or 'no reason given'}")
        if result.returncode:
            raise CommandFailure(argv, result)
        if checked.get("form") == "copy" and "changes" not in checked:
            checked["changes"] = []
        if not isinstance(checked.get("name"), str) or not isinstance(checked.get("changes"), list):
            raise Refusal("Temper's preset check has no name or changes")
        return checked

    def install_plan(self, root: Path, preset: str, environment: Mapping[str, str]) -> dict[str, Any]:
        """Temper's own review of a catalog preset's downloads, disk and fit; it saves nothing."""
        result = self.document(["init", "--dry-run", "--root", root, "--preset", preset], 300,
                               "temper-configuration-result/v1", environment=environment)
        return _plan(result.get("plan"), preset)

    def preset_plan(self, root: Path, preset: str, environment: Mapping[str, str]) -> dict[str, Any]:
        """The same review for a user preset in the root's presets.d."""
        result = self.document(["preset", "plan", preset, "--root", root], 300, "temper-preset-plan/v1",
                               environment=environment)
        return _plan(result.get("plan"), preset)

    def inspect(self, lock: Path) -> dict[str, Any]:
        execution = self.document(["execution", "inspect", "--lock", lock], 60, "temper-execution/v1")
        if not isinstance(execution.get("preset"), str) or execution.get("presets") != [execution["preset"]]:
            raise Refusal("Temper returned an unknown execution")
        return execution

    def prepare(self, lock: Path, root: Path, installation: str, timeout: float, environment: Mapping[str, str]) -> dict[str, Any]:
        material = self.document(["execution", "prepare", "--lock", lock, "--root", root, "--installation", installation],
                                 timeout, "temper-execution-material/v1", environment=environment, progress=True)
        if not isinstance(material.get("generation"), str) or not SHA256.fullmatch(material["generation"]):
            raise Refusal("Temper returned no prepared generation")
        if not isinstance(material.get("binding"), str) or not material["binding"].startswith("schema: temper-field-kit-binding/v1\n"):
            raise Refusal("Temper returned no material binding")
        return material

    def remove(self, lock: Path, root: Path, installation: str) -> dict[str, Any]:
        return self.document(["execution", "remove", "--lock", lock, "--root", root, "--installation", installation], 600)


def _plan(plan: object, preset: str) -> dict[str, Any]:
    if not isinstance(plan, dict) or type(plan.get("can_prepare")) is not bool \
            or not isinstance(plan.get("presets"), dict) or preset not in plan["presets"]:
        raise Refusal("Temper returned an unknown install plan")
    for key in ("download_bytes", "remaining_download_bytes", "fresh_disk_bytes", "remaining_disk_bytes", "free_disk_bytes"):
        if type(plan.get(key)) is not int or plan[key] < 0:
            raise Refusal(f"Temper's install plan has no {key}")
    if type(plan["presets"][preset].get("model_bytes")) is not int:
        raise Refusal("Temper's install plan has no model size")
    return plan


def _snapshot(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("sha256"), str) or not SHA256.fullmatch(value["sha256"]) \
            or type(value.get("sequence")) is not int or value["sequence"] <= 0:
        raise Refusal("Temper returned no active catalog")
    return value
