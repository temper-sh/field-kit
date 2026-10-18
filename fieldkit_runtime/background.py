"""A run that continues after the terminal or the agent's session closes.

`check --yes --background` starts the same check as a detached process that
writes to runs/background.log, and keeps the Mac awake while it runs.
`status` reports on it and on the newest run; `stop` asks it to stop the way
Ctrl-C does, so Temper still stops its processes and the attempt is kept.
"""
from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .common import Refusal, canonical_json
from .process import atomic_write

STATE = "background.json"
LOG = "background.log"
STOP_SECONDS = 300


def _state(runs: Path) -> dict[str, Any] | None:
    try:
        state = json.loads((runs / STATE).read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and type(state.get("pid")) is int else None


def alive(pid: int) -> bool:
    """Whether the process exists and is still a Field Kit check."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return "fieldkit_runtime" in result.stdout or "field-kit" in result.stdout


def running(repository: Path) -> dict[str, Any] | None:
    state = _state(repository / "runs")
    return state if state and alive(state["pid"]) else None


def start(repository: Path, arguments: list[str], say: Callable[[str], None] = print) -> int:
    runs = repository / "runs"
    current = running(repository)
    if current:
        raise Refusal(f"a background run is already going (process {current['pid']}); ./field-kit status shows it")
    runs.mkdir(exist_ok=True)
    with open(runs / LOG, "ab") as log:
        log.write(f"\n=== {_now()} field-kit check {' '.join(arguments)}\n".encode())
        log.flush()
        process = subprocess.Popen([str(repository / "field-kit"), "check", *arguments], cwd=repository,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    # caffeinate keeps the Mac from sleeping until the check ends.
    subprocess.Popen(["caffeinate", "-i", "-w", str(process.pid)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    atomic_write(runs / STATE, canonical_json({"pid": process.pid, "started_at": _now(), "arguments": arguments}))
    say(f"Field Kit is running in the background (process {process.pid}); the Mac stays awake until it ends.")
    say("You can close this terminal or your agent. ./field-kit status shows progress; ./field-kit stop stops it.")
    say(f"Log: {runs / LOG}")
    return 0


def _newest_run(runs: Path) -> dict[str, Any] | None:
    for path in sorted(runs.glob("*/run.json"), reverse=True):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return None


def status(repository: Path, cases: list[str], say: Callable[[str], None] = print) -> int:
    runs = repository / "runs"
    current = running(repository)
    if current:
        say(f"Background run: going since {current['started_at']} (process {current['pid']}).")
    run = _newest_run(runs)
    if run is None:
        say("No runs yet." if not current else "It has not created its run folder yet.")
    else:
        done = [row["id"] for row in run.get("cases") or []]
        line = f"Newest run: {run['id']}, {run['preset']['id']}, state {run['state']}"
        if run["state"] == "measuring":
            remaining = [case for case in cases if case not in done]
            line += f"; {len(done)} of {len(cases)} cases done" + (f", now {remaining[0]}" if remaining and current else "")
        say(line + ".")
        for row in run.get("cases") or []:
            say(f"  {row['id']}: {row['status']}")
        if run["state"] in ("preparing", "measuring", "cleaning") and not current:
            say("It is not running. Continue it with ./field-kit check, or in the background with"
                " ./field-kit check --yes --background.")
        elif run["state"] == "complete":
            say(f"Report: {runs / run['id'] / 'report.md'}")
    log = runs / LOG
    if log.is_file():
        tail = log.read_text(errors="replace").splitlines()[-5:]
        if tail:
            say("Last lines of the log:")
            for text in tail:
                say("  " + text)
    return 0


def stop(repository: Path, say: Callable[[str], None] = print, wait: float = STOP_SECONDS) -> int:
    current = running(repository)
    if not current:
        say("No background run is going. A run in a terminal stops with Ctrl-C there.")
        return 0
    os.kill(current["pid"], signal.SIGTERM)
    say(f"Asked process {current['pid']} to stop; Field Kit is stopping the engine and saving the attempt ...")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not alive(current["pid"]):
            say("Stopped. ./field-kit check continues the run later.")
            return 0
        time.sleep(2)
    say(f"Process {current['pid']} is still stopping; check again with ./field-kit status.")
    return 1


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
