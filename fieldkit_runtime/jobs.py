"""Detached jobs and blocking completion waits; sampling never needs agent turns."""
from __future__ import annotations
import datetime
import os
import signal
import subprocess
import time
from .common import Refusal
from .records import TERMINAL, AttemptFiles

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def process_identity(pid):
    result = subprocess.run(["/bin/ps", "-o", "lstart=", "-o", "stat=", "-p", str(pid)], capture_output=True,
                            text=True, timeout=5, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    fields = result.stdout.split()
    if result.returncode != 0 or len(fields) < 2 or fields[-1].startswith("Z"):
        return None
    return {"pid": pid, "started": " ".join(fields[:-1])}

def alive(identity):
    return bool(identity and process_identity(identity["pid"]) == identity)

def start(repository, study, run):
    files = AttemptFiles(repository / ".work" / "studies" / study / "runs" / run)
    with files.worker_log.open("ab") as log:
        process = subprocess.Popen([str(repository / "field-kit"), "_worker", study, run],
            cwd=repository, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    # The worker writes its own identity before any Temper effect.
    return process

def status(directory):
    files = AttemptFiles(directory)
    run = files.load_run()
    worker = run.get("worker")
    running = alive(worker)
    phase = run["state"]
    if phase not in TERMINAL and not running:
        phase = "queued" if not worker and time.time() - run["created_epoch"] < 10 else "needs-recovery"
    result = {"id": run["id"], "state": phase, "running": running,
            "candidate": run["candidate"], "reason": run["reason"],
            "elapsed_seconds": round(time.time() - run["created_epoch"], 1),
            "error": run.get("error"), "result": str(files.result) if files.result.exists() else None}
    result["observations"] = [{"session": row["id"], "steps": row.get("steps", []),
        "command": row.get("command"),
        "memory": {key: row.get("memory", {}).get(key) for key in
                   ("counter", "units", "engine_loaded_wired_bytes", "engine_peak_wired_bytes", "swap_growth_bytes")},
        "resource_issues": row.get("resources", {}).get("issues", []),
        "memory_samples": row.get("resources", {}).get("samples", 0),
        "resource_stops": row.get("resources", {}).get("stop_reasons", [])}
        for row in run.get("sessions", [])]
    return result

def wait(directory, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        result = status(directory)
        if (result["state"] in TERMINAL and not result["running"]) or result["state"] == "needs-recovery" or time.monotonic() >= deadline:
            return result
        time.sleep(min(1, max(0, deadline - time.monotonic())))

def stop(directory):
    run = AttemptFiles(directory).load_run()
    if run["state"] in TERMINAL:
        return status(directory)
    identity = run.get("worker")
    if not alive(identity):
        raise Refusal("worker is absent or its PID was reused; use study recover")
    os.kill(identity["pid"], signal.SIGTERM)
    return {"id": run["id"], "state": "stop-requested"}
