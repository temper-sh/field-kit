"""Proof that an attempt left nothing running. Recovery never repeats inference."""
from __future__ import annotations
import os
from . import jobs
from .common import Refusal, load_json
from .local_probe import LOCAL_STATUS_SCHEMA, group_alive
from .records import RUN_LIMIT, AttemptFiles

def require_shutdown(files: AttemptFiles) -> None:
    """Refuse unless every owned command exited and each serve's stop is proven, by Temper or Field Kit."""
    for command in [files.prepare_command, *(session.command for session in files.sessions())]:
        if not command.exists():
            continue
        record = load_json(command)
        if record.get("state") == "not-started":
            continue
        process = record.get("process")
        if record.get("state") == "starting" or (record.get("state") != "exited" and not process):
            raise Refusal(f"command startup is uncertain; inspect {command} before recovery")
        if jobs.alive(process):
            raise Refusal(f"owned command remains alive; inspect {command}")
        if process:
            try:
                os.killpg(process["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise Refusal(f"command group still exists; inspect {command}")
    for session in files.sessions():
        if not session.intent.exists():
            continue
        expected = load_json(session.intent)
        if not session.status.exists():
            # A completed observation may prove that startup failed before spawn.
            if session.observations.exists() and load_json(session.observations, RUN_LIMIT).get("safe_to_cleanup"):
                continue
            raise Refusal(f"probe startup is uncertain; inspect {session.stage}")
        proof = load_json(session.status)
        if proof.get("schema") == LOCAL_STATUS_SCHEMA:
            require_local_shutdown(session, expected, proof)
            continue
        if proof.get("schema") != "temper-probe-status/v1" or any(proof.get(k) != v for k, v in expected.items()) \
                or proof.get("state") != "stopped" or proof.get("safe_to_cleanup") is not True:
            raise Refusal(f"Temper has not confirmed shutdown; inspect {session.status}")

def require_local_shutdown(session, expected, proof):
    """Field Kit's own proof for a local engine, or the absence of its process group."""
    if any(proof.get(k) != v for k, v in expected.items()):
        raise Refusal(f"local engine status does not match its intent; inspect {session.status}")
    if proof.get("state") == "stopped" and proof.get("safe_to_cleanup") is True:
        return
    process = proof.get("process")
    if not isinstance(process, dict) or type(process.get("pgid")) is not int:
        raise Refusal(f"local engine startup is uncertain; inspect {session.stage}")
    if group_alive(process["pgid"]):
        raise Refusal(f"owned local engine still runs (process group {process['pgid']}); "
                      f"stop it with: kill -TERM -{process['pgid']}, then recover again")
