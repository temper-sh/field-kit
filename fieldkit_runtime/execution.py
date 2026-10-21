"""One supervised trial; request quality never gates other measurements."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from . import jobs, watcher, workloads
from .common import Refusal, canonical_json, load_json
from .measurements import watch_spec, rates, memory_record, classify_failure
from .probe import ManagedProbe, ProbeError
from .process import atomic_write, exclusive_lock, file_digest, run_process_progress, run_process_silent, runtime_identity
from .serving import monitored_call
from .splash_memory import SplashMemory
from .stream import CodingStream
from .temper import Temper

def free_listen():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return "127.0.0.1:" + str(sock.getsockname()[1])

def interrupt(signum, frame):
    raise KeyboardInterrupt("stop requested")

def save(directory, run):
    atomic_write(directory / "run.json", canonical_json(run))

def remaining(deadline, maximum):
    value = min(maximum, deadline - time.time())
    if value <= 0:
        raise TimeoutError("approved time budget exhausted")
    return value

def measure_session(directory, run, session, root, deadline, budgets):
    stage = directory / "sessions" / session["id"]
    stage.mkdir(parents=True, exist_ok=False)
    probe = ManagedProbe(temper=Path(run["temper"]["path"]), root=root, installation="field-kit",
        execution_lock=directory / "execution.lock.json", generation=run["preparation"]["generation"],
        listen=free_listen(), log_dir=stage / "process",
        watch_spec=watch_spec(run["resource_policy"], run["machine"], run["family"]),
        router_ready_seconds=min(30, remaining(deadline, 30)), log_bytes_max=16 * 1024**2)
    atomic_write(stage / "intent.json", canonical_json({"root": str(root), "installation": "field-kit",
        "generation": run["preparation"]["generation"], "listen": probe.listen}))
    row = {"id": session["id"], "steps": [], "failure": None, "safe_to_cleanup": False}
    loaded = native = None
    started = time.monotonic()
    try:
        probe.start()
        preset = run["execution"]["preset"]
        monitored_call(probe, f"/upstream/{preset}{workloads.health_path(run['family'])}", {},
                       remaining(deadline, 1800), method="GET")
        if not probe.observe_engine():
            raise ProbeError("engine identity unavailable after readiness")
        row["startup_seconds"] = time.monotonic() - started
        loaded = watcher.sample_macos(probe.full_binding, 15)
        if run["family"] == "splash":
            source = f"/upstream/{preset}/status"
            native = SplashMemory(lambda: monitored_call(probe, source, {}, 2, method="GET"), source, 5)
            native.capture("loaded")
        if "command" in session:
            environment = dict(os.environ)
            output = stage / "output"
            output.mkdir()
            environment.update(FIELD_KIT_BASE_URL=f"http://{probe.listen}/v1",
                               FIELD_KIT_MODEL=preset, FIELD_KIT_OUTPUT_DIR=str(output))
            task_started = time.monotonic()
            def observe():
                probe.ensure_healthy()
                probe.validate_owned_boundary()
                if native:
                    native.poll()
            result = run_process_silent(session["command"], remaining(deadline, budgets["trial_seconds"]),
                environment=environment, identity_path=stage / "command.json", observer=observe)
            atomic_write(stage / "stdout.txt", result.stdout)
            atomic_write(stage / "stderr.txt", result.stderr)
            row["command"] = {"argv": session["command"], "cwd": session["cwd"], "returncode": result.returncode,
                              "task_seconds": time.monotonic() - task_started, "quality": "needs source/deliverable review",
                              "native_rates": None}
            if result.stop_reason:
                row["failure"] = {"kind": result.stop_reason if result.stop_reason in ("timeout", "interrupted") else "runtime-failure",
                                  "message": result.stop_reason}
        else:
            previous_request = previous_response = None
            for step in session["steps"]:
                folder = stage / step["id"]
                folder.mkdir()
                request = copy.deepcopy(step["request"])
                if step.get("append_previous"):
                    message = previous_response["message"]
                    request["messages"] = previous_request["messages"] + [
                        {"role": "assistant", "content": message["content"]}] + request["messages"]
                body = {**request, "model": preset, "stream": True, "stream_options": {"include_usage": True}}
                atomic_write(folder / "request.json", canonical_json(body))
                stream = CodingStream(folder / "response.sse")
                observed = {"id": step["id"], "failure": None}
                try:
                    response = monitored_call(probe, "/v1/chat/completions", body,
                        remaining(deadline, budgets["request_seconds"]), streaming=True, stream_reader=stream,
                        observer=native.poll if native else None)
                except (Exception, KeyboardInterrupt) as error:
                    observed["failure"] = {"kind": classify_failure(error), "message": str(error)}
                    response = stream.snapshot()
                atomic_write(folder / "response.json", canonical_json(response))
                observed.update(rates(response), elapsed_seconds=response.get("service_seconds"),
                    first_token_seconds=response.get("first_token_seconds"),
                    first_answer_seconds=response.get("first_answer_seconds"),
                    response_complete=response.get("stream_complete"),
                    finish_reason=response.get("finish_reason"),
                    correct=workloads.correct(response, step.get("expect")),
                    cached_tokens=(response.get("usage") or {}).get("prompt_tokens_details", {}).get("cached_tokens"))
                row["steps"].append(observed)
                atomic_write(stage / "observations.json", canonical_json(row))
                previous_request, previous_response = request, response
                if observed["failure"]:
                    row["failure"] = observed["failure"]
                    break
                # Incorrect/truncated outputs remain measured; independent requests still run.
    except (Exception, KeyboardInterrupt) as error:
        row["failure"] = {"kind": classify_failure(error), "message": str(error)}
    finally:
        try:
            if native:
                native.capture("final")
        except (Exception, KeyboardInterrupt):
            pass
        resources = probe.finish()
        row["resources"] = resources
        row["safe_to_cleanup"] = resources.get("safe_to_cleanup", False)
        row["memory"] = memory_record(loaded, resources, native.result() if native else None)
        row["memory"]["counter"] = "system-wired-growth"
        row["memory"]["units"] = "bytes"
        row["memory"]["limitations"] = "System wired growth includes other apps; process counters may omit GPU memory. Five-second sampling can miss peaks. Counters overlap."
        row["elapsed_seconds"] = time.monotonic() - started
        atomic_write(stage / "observations.json", canonical_json(row))
    return row

def execute(study, directory):
    machine_lock = study.repository / ".work" / "execution.lock"
    machine_lock.parent.mkdir(exist_ok=True)
    machine_lock.touch(exist_ok=True)
    with exclusive_lock(machine_lock):
        return _execute(study, directory)

def _execute(study, directory):
    with exclusive_lock(study.directory / ".lock"):
        run = load_json(directory / "run.json", 64 * 1024**2)
        if run["state"] != "queued":
            raise Refusal("attempt already started; recover it and create a new trial")
        state = study.load()
        if file_digest(directory / "execution.lock.json") != run["lock_sha256"] or file_digest(directory / "workload.json") != run["workload_sha256"]:
            raise Refusal("attempt inputs changed")
        temper = Temper(Path(run["temper"]["path"]))
        if temper.identity() != run["temper"]:
            raise Refusal("Temper changed after this attempt was reserved; prepare a new attempt")
        root = study.runtime(run["candidate"])
        cache = study.directory / ".runtime" / "hf-cache"
        if cache.is_symlink() or (cache / "hub").is_symlink():
            raise Refusal("study cache was replaced with a symlink; retain it for repair")
        run.update(worker=jobs.process_identity(os.getpid()), field_kit=runtime_identity(),
                   state="preparing", safe_to_cleanup=False)
        save(directory, run)
    previous = signal.signal(signal.SIGTERM, interrupt)
    awake = None
    deadline = min(state["deadline"], time.time() + state["budgets"]["trial_seconds"])
    environment = dict(os.environ)
    environment.update(HF_HOME=str(cache), HF_HUB_CACHE=str(cache / "hub"), HF_HUB_DISABLE_TELEMETRY="1")
    try:
        if sys_platform_macos():
            awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        root.mkdir(parents=True, exist_ok=True)
        def preparing(argv, timeout, **kwargs):
            return run_process_progress(argv, timeout, identity_path=directory / "prepare-command.json", **kwargs)
        temper.progress_runner = preparing
        prepared = temper.prepare(directory / "execution.lock.json", root, "field-kit",
                                  remaining(deadline, 21600), environment)
        if prepared.get("execution") != run["execution"]:
            raise Refusal("Temper prepared a different execution")
        run["preparation"] = {"generation": prepared["generation"]}
        run.update(state="measuring", safe_to_cleanup=True)
        save(directory, run)
        work = workloads.load(directory / "workload.json")
        sessions = [{"id": "task", "command": work["command"], "cwd": work["cwd"]}] if "command" in work else work["sessions"]
        for session in sessions:
            run["safe_to_cleanup"] = False
            save(directory, run)
            # The CLI worker owns its cwd; no concurrent task shares this process.
            old_directory = Path.cwd()
            try:
                if "command" in session:
                    os.chdir(session["cwd"])
                row = measure_session(directory, run, session, root, deadline, state["budgets"])
            finally:
                os.chdir(old_directory)
            run["sessions"].append(row)
            run["safe_to_cleanup"] = row["safe_to_cleanup"]
            save(directory, run)
            if row["failure"] or not row["safe_to_cleanup"] or row["resources"].get("stop_reasons"):
                run["error"] = row["failure"] or {"kind": "resource-stop", "message": "inspect session resources"}
                break
        run["state"] = outcome(run)
    except (Exception, KeyboardInterrupt) as error:
        run["error"] = {"kind": classify_failure(error), "message": str(error)}
        try:
            recover_shutdown(directory, run, current_worker=True)
            run["safe_to_cleanup"] = True
        except Refusal:
            run["safe_to_cleanup"] = False
        run["state"] = outcome(run)
    finally:
        signal.signal(signal.SIGTERM, previous)
        if awake:
            awake.terminate()
            awake.wait(timeout=5)
        run["finished_at"] = jobs.now()
        save(directory, run)
        atomic_write(directory / "result.json", canonical_json(run))
        if run.get("error"):
            atomic_write(directory / "failure.md", (
                "# Trial failure\n\nRun: " + run["id"] + "\n\n" + json.dumps(run["error"], indent=2) +
                "\n\nDiagnosis and repair: pending agent review. Attach the repair with field-kit repair.\n").encode())
    return jobs.status(directory)

def sys_platform_macos():
    import sys
    return sys.platform == "darwin"

def outcome(run):
    if not run["safe_to_cleanup"]:
        return "retained"
    error = run.get("error")
    return "interrupted" if error and error.get("kind") == "interrupted" else "failed" if error else "complete"

def recover_shutdown(directory, run, current_worker=False):
    for command in [directory / "prepare-command.json", *directory.glob("sessions/*/command.json")]:
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
    for intent in directory.glob("sessions/*/intent.json"):
        expected = load_json(intent)
        status = intent.parent / "process" / "temper-status.json"
        if not status.exists():
            # A completed observation may prove that startup failed before spawn.
            observed = intent.parent / "observations.json"
            if observed.exists() and load_json(observed, 64 * 1024**2).get("safe_to_cleanup"):
                continue
            raise Refusal(f"probe startup is uncertain; inspect {intent.parent}")
        proof = load_json(status)
        if proof.get("schema") != "temper-probe-status/v1" or any(proof.get(k) != v for k, v in expected.items()) \
                or proof.get("state") != "stopped" or proof.get("safe_to_cleanup") is not True:
            raise Refusal(f"Temper has not confirmed shutdown; inspect {status}")
