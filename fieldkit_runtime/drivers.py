"""What sends the work to a served preset: frozen requests, or an existing harness.

A driver fills its session row; the attempt owns the serve around it.
"""
from __future__ import annotations
import os
import sys
import time
from . import records
from .process import atomic_write, run_process_silent

class Requests:
    """Frozen request steps in one serve. Incorrect or truncated answers stay measured
    and later steps still run; only a transport or runtime failure ends the session."""
    def __init__(self, steps, interface, model):
        self.steps, self.interface, self.model = steps, interface, model

    def run(self, engine, files, row, attempt):
        previous = None
        for step in self.steps:
            step_files = files.step(step["id"])
            step_files.folder.mkdir()
            request, body = self.interface.build(step, engine.preset, self.model, previous)
            records.write(step_files.request, body)
            response, error = self.interface.send(engine, step_files, body, attempt.limit("request_seconds"))
            observed = records.step_row(step["id"])
            if error is not None:
                observed["failure"] = records.failure(error)
            response, measured = self.interface.observe(response, body, step, attempt.engine, self.model)
            if response is not None:  # nothing arrived; the failure says why
                records.write(step_files.response, response)
            observed.update(measured)
            row["steps"].append(observed)
            records.write(files.observations, row)
            previous = (request, response)
            if observed["failure"]:
                row["failure"] = observed["failure"]
                return

class Harness:
    """An installed completed-task harness pointed at the served preset. It owns its
    conversation, tool execution and judgments; Field Kit owns processes and resources."""
    def __init__(self, command, cwd, model):
        self.command, self.cwd, self.model = command, cwd, model

    def run(self, engine, files, row, attempt):
        files.output.mkdir()
        environment = dict(os.environ)
        environment.update(FIELD_KIT_BASE_URL=engine.base_url, FIELD_KIT_NATIVE_URL=engine.native_url,
                           FIELD_KIT_MODEL=engine.preset, FIELD_KIT_OUTPUT_DIR=str(files.output))
        for name, value in (("FIELD_KIT_MODEL_ADAPTER", self.model.identifier if self.model else None),
                            ("FIELD_KIT_CONTEXT_WINDOW_TOKENS", engine.subject.context_window())):
            if value:
                environment[name] = str(value)
            else:
                environment.pop(name, None)
        # A workload names Field Kit's own Python as {python}, so it stays portable between checkouts.
        argv = [sys.executable if value == "{python}" else value for value in self.command]
        started = time.monotonic()
        result = run_process_silent(argv, attempt.limit("trial_seconds"), environment=environment,
                                    identity_path=files.command, observer=engine.observe, cwd=self.cwd)
        atomic_write(files.stdout, result.stdout)
        atomic_write(files.stderr, result.stderr)
        row["command"] = {"argv": argv, "cwd": self.cwd, "returncode": result.returncode,
                          "task_seconds": time.monotonic() - started, "quality": "needs source/deliverable review",
                          "native_rates": None}
        if result.stop_reason:
            row["failure"] = {"kind": result.stop_reason if result.stop_reason in ("timeout", "interrupted") else "runtime-failure",
                              "message": result.stop_reason}

def for_workload(work, interface, model):
    """(session ID, driver) pairs. Each session gets a fresh serve."""
    if "command" in work:
        return [("task", Harness(work["command"], work["cwd"], model))]
    return [(session["id"], Requests(session["steps"], interface, model)) for session in work["sessions"]]
