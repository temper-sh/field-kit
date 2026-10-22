"""Return an idle study, including failures and repairs, without private runtimes."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
import zipfile
from .common import Refusal, canonical_json
from .process import exclusive_lock, file_digest
from .records import AttemptFiles

def evidence_files(directory):
    # Exclude only the owned runtime at the study root. A directory named
    # candidate/fixture deeper in evidence may contain indispensable test logs.
    def visit(path):
        if path.is_symlink():
            raise Refusal(f"evidence symlink needs deliberate materialization or removal: {path}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                yield from visit(child)
        elif path.is_file():
            yield path
        else:
            raise Refusal(f"unsupported evidence file: {path}")
    for path in sorted(directory.iterdir()):
        if path.name in (".runtime", ".lock"):
            continue
        yield from visit(path)

def export_study(study, destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise Refusal("export destination exists; choose a new file")
    if destination.is_relative_to(study.directory):
        raise Refusal("put the archive outside the study evidence")
    with exclusive_lock(study.directory / ".lock"):
        state = study.load()
        # Returning a blocked study is useful. Only active writers block export.
        from . import jobs
        for run_id in state["runs"]:
            files = AttemptFiles(study.path_for(run_id))
            row = files.load_run()
            if jobs.alive(row.get("worker")) or jobs.status(files.directory)["state"] == "queued":
                raise Refusal("wait for or stop the active worker before export")
            for path, expected in files.inputs(row):
                if file_digest(path) != expected:
                    raise Refusal(f"attempt input changed: {run_id}/{path.name}")
        files = list(evidence_files(study.directory))
        manifest = {str(path.relative_to(study.directory)): {"bytes": path.stat().st_size, "sha256": file_digest(path)}
                    for path in files}
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".field-kit-export-", dir=destination.parent) as tmp:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, Path(study.name) / path.relative_to(study.directory))
                archive.writestr(study.name + "/export-manifest.json", canonical_json(manifest))
            with zipfile.ZipFile(tmp.name) as archive:
                if archive.testzip():
                    raise Refusal("archive CRC check failed")
                from .common import digest
                for name, record in manifest.items():
                    raw = archive.read(study.name + "/" + name)
                    if len(raw) != record["bytes"] or digest(raw) != record["sha256"]:
                        raise Refusal("evidence changed while exporting; retry after writers finish")
            try:
                os.link(tmp.name, destination)
            except FileExistsError as error:
                raise Refusal("destination appeared during export; nothing replaced") from error
    return {"archive": str(destination), "study_state": state["state"], "files": len(files),
            "includes": "attempts, task outputs, study notes and repair reports"}
