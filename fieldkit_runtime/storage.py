"""Where study material lives. Temper downloads and verifies; Field Kit chooses,
records and cleans locations, removing only exact files a study added."""
from __future__ import annotations
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from . import lockview
from .common import Refusal

GIB = 1024**3
INTERNAL_RESERVE_BYTES = 10 * GIB
SYSTEM = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}

def default_cache(environ=os.environ):
    """Temper's own order: HF_HUB_CACHE, its legacy alias, HF_HOME, XDG_CACHE_HOME, ~/.cache."""
    root = environ.get("HF_HUB_CACHE") or environ.get("HUGGINGFACE_HUB_CACHE")
    if not root:
        base = environ.get("HF_HOME") or str(Path(environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "huggingface")
        root = str(Path(base) / "hub")
    return Path(os.path.expandvars(root)).expanduser().absolute()

def _mounts():
    # macOS: "/dev/disk4s2 on /Volumes/Crucial X10 (exfat, local, ...)"
    result = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=15, env=SYSTEM)
    rows = []
    for line in result.stdout.splitlines():
        device, separator, rest = line.partition(" on ")
        point, opened, options = rest.rpartition(" (")
        if separator and opened:
            rows.append((device, point, options.rstrip(")").split(",")[0].strip()))
    return rows

def describe(path):
    """Volume facts for a location or its nearest existing parent. Unknown stays None."""
    planned = Path(path).absolute()
    existing = planned
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    record = {"path": str(planned), "exists": planned.exists(), "mount_point": None, "filesystem": None,
              "device": None, "internal": None, "protocol": None, "free_bytes": None}
    try:
        record["free_bytes"] = shutil.disk_usage(existing).free
        if sys.platform != "darwin":
            return record
        resolved = existing.resolve()
        mounts = [row for row in _mounts() if Path(row[1]) == resolved or Path(row[1]) in resolved.parents]
    except (OSError, subprocess.SubprocessError):
        return record
    if mounts:
        device, point, filesystem = max(mounts, key=lambda row: len(row[1]))
        record.update(device=device, mount_point=point, filesystem=filesystem)
        try:
            info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", device],
                                                 capture_output=True, timeout=30, env=SYSTEM).stdout)
            record.update(internal=info.get("Internal"), protocol=info.get("BusProtocol"))
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
            pass
    return record

def require_apfs(record, purpose):
    if record["filesystem"] not in (None, "apfs"):
        raise Refusal(f"{purpose} must be on APFS, not {record['filesystem']} at {record['mount_point']}: "
                      "Temper preparation failed on exFAT. Use an internal folder or an APFS disk image.")

def _places(cache, entry):
    repository = Path(cache) / ("models--" + entry["repo"].replace("/", "--"))
    return repository, repository / "snapshots" / entry["revision"] / entry["path"], repository / "blobs" / entry["sha256"]

def present(cache, entry):
    """Temper's test: the exact snapshot file or the same repository's SHA-256 blob."""
    _, snapshot, blob = _places(cache, entry)
    return snapshot.is_file() or blob.is_file()

def weights_plan(cache, locks, cleanup):
    files = {}
    for lock in locks:
        files.update(lockview.hf_files(lock))
    rows = [{**entry, "present_before": present(cache, entry)} for entry in files.values()]
    return {"cache": str(cache), "cleanup": cleanup,
            "absent_bytes": sum(row["bytes"] for row in rows if not row["present_before"]),
            "files": sorted(rows, key=lambda row: (row["repo"], row["path"]))}

def require_space(root, cache, absent_bytes, software_bytes):
    """Known sizes only. exFAT caches may keep full snapshot copies instead of links."""
    factor = 2 if cache["filesystem"] == "exfat" else 1
    needs = {}
    for record, amount in ((cache, absent_bytes * factor), (root, software_bytes + INTERNAL_RESERVE_BYTES)):
        if record is None:
            continue
        key = record["mount_point"] or record["path"]
        needs[key] = (record, needs.get(key, (record, 0))[1] + amount)
    for key, (record, amount) in needs.items():
        if record["free_bytes"] is not None and record["free_bytes"] < amount:
            raise Refusal(f"not enough free disk at {key}: about {amount / GIB:.1f} GiB needed "
                          f"(known files, exFAT copies and a 10 GiB reserve), {record['free_bytes'] / GIB:.1f} GiB free")
    return {key: amount for key, (record, amount) in needs.items()}

def require_cache(cache, explicit):
    if explicit and not Path(cache).is_dir():
        raise Refusal(f"weights cache is unavailable: {cache}. Connect its disk, then start a new attempt.")

def remove_added(cache, files):
    """Remove exact study-added snapshot files and unreferenced blobs. Repositories,
    refs and every file present before the study stay; a repository can hold the user's files too."""
    removed, kept = [], []
    root = Path(cache).resolve()
    for entry in files:
        if entry.get("present_before") or not lockview.valid_file(entry):
            continue
        repository, snapshot, blob = _places(root, entry)
        if any(path.is_symlink() for path in (repository, repository / "snapshots", repository / "blobs")):
            kept.append({**_name(entry), "reason": "cache directory is a symlink"})
            continue
        if snapshot.is_symlink():
            if snapshot.resolve() != blob.resolve():
                kept.append({**_name(entry), "reason": "snapshot points elsewhere"})
                continue
            snapshot.unlink()
            removed.append(str(snapshot))
        elif snapshot.is_file():
            if snapshot.stat().st_size != entry["bytes"]:
                kept.append({**_name(entry), "reason": "snapshot size differs from the lock"})
                continue
            snapshot.unlink()
            removed.append(str(snapshot))
        if blob.is_file() and not blob.is_symlink():
            if _referenced(repository, blob):
                kept.append({**_name(entry), "reason": "blob used by another revision"})
            else:
                blob.unlink()
                removed.append(str(blob))
        _prune(snapshot.parent, repository / "snapshots")
    return {"removed": removed, "kept": kept}

def _name(entry):
    return {"repo": entry["repo"], "revision": entry["revision"], "path": entry["path"]}

def _referenced(repository, blob):
    target = blob.resolve()
    for path in (repository / "snapshots").rglob("*") if (repository / "snapshots").is_dir() else []:
        if path.is_symlink() and path.resolve() == target:
            return True
    return False

def _prune(directory, stop):
    while directory != stop and stop in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent
