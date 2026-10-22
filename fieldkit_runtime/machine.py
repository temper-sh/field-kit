"""Exclusive use of the machine. Temper owns the user's layout; Field Kit stops
only the approved one and never signals a process it does not own."""
from __future__ import annotations
import os
import shlex
import subprocess
from .common import Refusal
from .process import CommandFailure

LARGE_PROCESS_BYTES = 1024**3
INSTRUCTION = "While Field Kit runs, do not use Temper, and close other AI apps."

def layout_record(status, root):
    """What runs in one Temper root. A desired layout that is not observed is not running."""
    observed = status["observed"]
    job = status["state"].get("current") or status["state"].get("desired") or {}
    running = bool(observed.get("exists") or observed.get("ready"))
    return {"root": root, "running": running, "busy": bool(observed.get("busy")),
            "layout": job.get("layout") if running else None,
            "name": job.get("name") if running else None,
            "listen": job.get("listen") if running else None}

def restore_command(record):
    if not record.get("layout"):
        return None
    argv = ["temper", "layout", "activate", record["layout"], *(["--root", record["root"]] if record.get("root") else [])]
    return " ".join(shlex.quote(value) for value in argv)

def restore_text(study_id, record):
    command = restore_command(record)
    lines = ["# Return to normal work", "", "After a failure, stop the study first:", "",
             f"    ./field-kit study stop {study_id}", ""]
    if command:
        lines += ["Then start your usual Temper layout again:", "", f"    {command}", "",
                  "If that does not work, restart the Mac and run the same command.",
                  "Temper starts no layout at login, so nothing from the study runs after a restart."]
    else:
        lines += ["Field Kit did not stop a Temper layout. A restart also ends every study process."]
    lines += ["", "The study's evidence stays in place for later recovery and export.", ""]
    return "\n".join(lines).encode()

def observe(temper, root):
    """The layout record, or an explicit unknown when Temper cannot report it."""
    try:
        return layout_record(temper.layout_status(root), root)
    except (CommandFailure, Refusal) as error:
        return {"root": root, "running": None, "busy": None, "layout": None, "name": None, "listen": None,
                "error": str(error)}

def claim(temper, approved):
    """Stop the approved layout before measuring. Anything else running is the tester's to stop."""
    root = approved.get("root")
    current = observe(temper, root)
    if current["running"] is None:
        if approved.get("layout"):
            raise Refusal(f"cannot read Temper layout status ({current['error']}); the approved layout may still run")
        return False
    if not current["running"]:
        return False
    if not approved.get("layout") or current["layout"] != approved["layout"]:
        raise Refusal(f"Temper layout {current['layout']!r} is running but was not approved for stopping. "
                      "Stop it with temper layout stop; " + INSTRUCTION)
    try:
        temper.layout_stop(root)
    except CommandFailure as error:
        raise Refusal(f"could not stop layout {current['layout']} ({error}); retry when its requests finish") from error
    if layout_record(temper.layout_status(root), root)["running"]:
        raise Refusal("the user layout still runs after stop; inspect temper layout status")
    return True

def restore(temper, approved):
    """Start the stopped layout again, unless the tester already did."""
    root = approved.get("root")
    if not layout_record(temper.layout_status(root), root)["running"]:
        temper.layout_activate(approved["layout"], root)

def large_processes(minimum=LARGE_PROCESS_BYTES):
    """Read-only list for the tester. RSS can omit GPU memory; nothing is signaled."""
    try:
        result = subprocess.run(["/bin/ps", "-axo", "pid=,rss=,comm="], capture_output=True, text=True,
                                timeout=15, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            pid, rss = int(fields[0]), int(fields[1]) * 1024
            if rss >= minimum and pid != os.getpid():
                rows.append({"pid": pid, "name": os.path.basename(fields[2]), "rss_bytes": rss})
    return sorted(rows, key=lambda row: -row["rss_bytes"])
