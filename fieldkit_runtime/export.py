"""Export a closed study and its referenced evidence without runtime caches."""
from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
import tempfile
import zipfile

from .common import Refusal, load_json
from .process import exclusive_lock, file_digest
from .study import STUDY_SCHEMA, _identity


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"missing or unsafe evidence file: {path}")


def _tree(path: Path):
    if path.is_symlink():
        raise Refusal(f"refusing evidence symlink: {path}")
    if path.is_dir():
        for child in sorted(path.iterdir()):
            # Reconstructible copies may remain after failed grading. Keep the
            # submissions and test logs, not vendored source or executed trees.
            if child.name in ("candidate", "fixture", "__pycache__"):
                continue
            yield from _tree(child)
    else:
        _regular(path)
        yield path


def export_study(repository: Path, identity: str, destination: Path) -> Path:
    repository = repository.resolve()
    study = repository / "studies" / _identity(identity)
    runs = repository / "runs"
    if study.is_symlink() or study.parent.is_symlink() or runs.is_symlink() or not study.is_dir():
        raise Refusal("missing study or unsafe evidence directory")
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise Refusal("export destination already exists; choose a new file")
    with exclusive_lock(study / ".lock"), ExitStack() as locks:
        _regular(study / "study.json")
        state = load_json(study / "study.json", 64 * 1024**2)
        if state.get("schema") != STUDY_SCHEMA or state.get("id") != identity or state.get("state") != "closed":
            raise Refusal("close the study before exporting its complete evidence")
        files = [study / "study.json", study / "report.md"]
        if (study / "conclusions.md").exists():
            files.append(study / "conclusions.md")
        seen = set()
        for entry in state["runs"]:
            run_id = _identity(entry["id"])
            if run_id in seen:
                raise Refusal(f"duplicate referenced run: {run_id}")
            seen.add(run_id)
            directory = runs / run_id
            if directory.is_symlink() or not directory.is_dir():
                raise Refusal(f"missing or unsafe referenced run: {run_id}")
            locks.enter_context(exclusive_lock(directory / ".lock"))
            required = [directory / name for name in ("run.json", "result.json", "execution.lock.json", "report.md")]
            for path in required:
                _regular(path)
            result = load_json(directory / "result.json", 64 * 1024**2)
            run = load_json(directory / "run.json", 64 * 1024**2)
            for record in (result, run):
                if record.get("id") != run_id or record.get("study") != {"id": identity, "candidate": entry["candidate"]}:
                    raise Refusal(f"referenced run belongs to different study/candidate: {run_id}")
                if record.get("state") not in ("complete", "cancelled"):
                    raise Refusal(f"referenced run is unfinished: {run_id}")
                if record.get("lock_sha256") != file_digest(directory / "execution.lock.json"):
                    raise Refusal(f"execution lock no longer matches run: {run_id}")
            # Missing reviews and failed grading remain missing evidence. They
            # must not prevent returning the raw attempt for diagnosis.
            for row in result.get("cases", []):
                case = directory / "cases" / _identity(row["id"])
                if case.is_symlink() or not case.is_dir():
                    raise Refusal(f"missing or unsafe case evidence: {case}")
            files += required
            for name in ("cases", "review", "review.json"):
                path = directory / name
                if path.exists() or path.is_symlink():
                    files.extend(_tree(path))
        for path in files:
            _regular(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".study-export-", dir=destination.parent) as temporary:
            with zipfile.ZipFile(temporary.name, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, path.relative_to(repository))
            with zipfile.ZipFile(temporary.name) as archive:
                if archive.testzip() is not None:
                    raise Refusal("export archive failed its integrity check")
            # Atomic no-replace publication. Existing exports are never truncated.
            try:
                os.link(temporary.name, destination)
            except FileExistsError as error:
                raise Refusal("export destination appeared during export; nothing replaced") from error
    return destination
