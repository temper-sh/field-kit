"""Proof that an attempt left nothing running. Recovery never repeats inference."""
from __future__ import annotations
import os
from . import jobs, records
from .common import Refusal, digest, load_json, read_regular
from .local_probe import LOCAL_STATUS_SCHEMA, group_alive
from .records import RUN_LIMIT, AttemptFiles

def require_shutdown(files: AttemptFiles, verify_stopped=None) -> None:
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
        proof = load_json(session.status, 16 * 1024**2)
        if proof.get("schema") == LOCAL_STATUS_SCHEMA:
            require_local_shutdown(session, expected, proof)
            continue
        if proof.get("schema") != "temper-probe-status/v1" or any(proof.get(k) != v for k, v in expected.items()) \
                or proof.get("state") != "stopped":
            raise Refusal(f"Temper has not confirmed shutdown; inspect {session.status}")
        if proof.get("safe_to_cleanup") is True:
            continue
        if session.recovery.exists():
            validate_recovery(session, proof, load_json(session.recovery))
        elif verify_stopped is not None:
            observation = verify_stopped(session.status)
            validate_recovery(session, proof, observation)
            records.write(session.recovery, observation)
        else:
            raise Refusal(f"Temper has not confirmed shutdown; use study recover with a compatible Temper; inspect {session.status}")


def validate_recovery(session, status, observation):
    """A fresh Temper read refers to these exact failed status bytes, not another serve."""
    if observation.get("schema") != "temper-probe-recovery/v1" or observation.get("safe_to_cleanup") is not True \
            or observation.get("status_sha256") != digest(read_regular(session.status, 16 * 1024**2)) \
            or any(observation.get(key) != status.get(key) for key in ("root", "installation", "generation")) \
            or not observation.get("verified_at") or not isinstance(observation.get("temper"), dict):
        raise Refusal(f"shutdown recovery observation does not match the original status; inspect {session.recovery}")
    processes = [*status.get("roles", []), *status.get("processes", [])]
    pids = {status.get("temper_pid"), status.get("process_group_id"), *(process.get("pid") for process in processes)}
    groups = {status.get("process_group_id"), *(process.get("pgid") for process in processes)}
    if not pids.issubset(observation.get("process_ids", [])) or not groups.issubset(observation.get("process_group_ids", [])) \
            or status.get("listen") not in observation.get("listeners", []):
        raise Refusal(f"shutdown recovery observation omitted the recorded process/listener boundary; inspect {session.recovery}")

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
