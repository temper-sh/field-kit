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

MINIMUM_VERSION = (0, 3, 0)
RESPONSE_LIMIT = 16 * 1024 * 1024
VERSION = re.compile(r"temper (\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?")


def locate(explicit: Path | None = None) -> Path:
    """The Temper to use: --temper, else `temper` on PATH. A link, such as
    Homebrew's, resolves to its file, so a run records the binary itself."""
    if explicit is None:
        found = shutil.which("temper")
        if found is None:
            raise Refusal("Temper was not found. Use --temper PATH for an existing installation; see docs/TEMPER.md for setup.")
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
            raise Refusal(f"Field Kit needs the execution commands from Temper 0.3.0 or newer; found {text}."
                          " See docs/TEMPER.md; use a compatible installed binary before considering an upgrade.")
        return {"path": str(self.path), "version": text, "sha256": file_digest(self.path)}

    def machine_facts(self) -> dict[str, Any]:
        facts = self.document(["machine", "facts"], 60, "temper-machine-facts/v1")
        for key in ("physical_memory_bytes", "wired_limit_mib"):
            if type(facts.get(key)) is not int or facts[key] <= 0:
                raise Refusal(f"Temper machine facts have no positive {key}")
        return facts

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

    def supports(self, command: list[str]) -> bool:
        """Whether this installed Temper offers a command, judged by its help."""
        try:
            response = self.runner([str(self.path), *command, "--help"], 30)
        except (CommandFailure, Refusal, OSError):
            return False
        text = (response.stdout + response.stderr).decode(errors="replace")
        return response.returncode == 0 and ("Usage:" in text or "usage:" in text)

    def check_preset(self, preset: str, root: Path, environment: Mapping[str, str]) -> dict[str, Any]:
        return self.document(["preset", "check", preset, "--root", root], 120, "temper-preset-check/v1",
                             environment=environment)

    def plan_preset(self, preset: str, root: Path, environment: Mapping[str, str]) -> dict[str, Any]:
        return self.document(["preset", "plan", preset, "--root", root], 300, "temper-preset-plan/v1",
                             environment=environment)

    def layout_status(self, root: str | None = None) -> dict[str, Any]:
        status = self.document(["layout", "status", *root_arguments(root)], 60)
        if not isinstance(status.get("state"), dict) or not isinstance(status.get("observed"), dict):
            raise Refusal("Temper printed an unknown layout status")
        return status

    def layout_stop(self, root: str | None = None) -> dict[str, Any]:
        return self.document(["layout", "stop", *root_arguments(root)], 300)

    def layout_activate(self, layout: str, root: str | None = None) -> dict[str, Any]:
        return self.document(["layout", "activate", layout, *root_arguments(root)], 900)


def root_arguments(root: str | None) -> list[str]:
    """No root means Temper's own default, the user's ~/.temper."""
    return ["--root", root] if root else []
