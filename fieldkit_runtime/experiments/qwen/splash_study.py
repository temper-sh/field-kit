"""The 36 GiB ceiling and 48 GiB+ engine/quant study; one action per cell."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import signal
import subprocess
import time

from ...catalog import Refusal, canonical_json
from ...execution import configure_execution, inspect_execution, validate_material
from ...probe import ManagedProbe, ProbeError
from ...workflow import _atomic_write, run_process_silent, CommandFailure
from .coding import CodingStream, native_count, performance, request_body
from .evaluation import evaluate, unpack
from .measurement import construct_context, monitored_call
from .method import grade
from .patches import InvalidPatch, apply, submission
from .splash_memory import SplashMemory

SCHEMA = "field-kit-qwen-splash-study/v1"
SELECTOR = "qwen-machine-study@4"
FIELDS = ["completed-work", "context", "fit", "interaction", "limits", "profile", "tuning"]


def bucket(facts):
    memory = facts["physical_memory_bytes"]
    if memory == 36 * 1024**3:
        return "36"
    if memory >= 48 * 1024**3:
        return "48-plus"
    raise Refusal("this study measures 36 GiB or 48 GiB+ Macs; 32 GiB is existing reference evidence")


def matrix(protocol, facts):
    selected = bucket(facts)
    return [cell for cell in protocol["cells"] if selected in cell["buckets"]]


def next_cell(cells, rows):
    completed = {row["id"] for row in rows}
    context_stopped = any(row["kind"] == "context" and row["status"] != "passed" for row in rows)
    return next((cell for cell in cells if cell["id"] not in completed
                 and not (cell["kind"] == "context" and context_stopped)), None)


def classify_failure(error):
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    message = str(error).lower()
    if any(text in message for text in ("out of memory", "out-of-memory", "insufficient memory", "allocation failed")):
        return "memory-failure"
    return "runtime-failure"


def context_summary(rows):
    points = [row for row in rows if row["kind"] == "context"]
    passed = [row for row in points if row["status"] == "passed"]
    return {"highest_successful_window_tokens": max((row["window"] for row in passed), default=None),
            "highest_successful_input_tokens": max((row["target_input_tokens"] for row in passed), default=None),
            "first_unsuccessful_window_tokens": next((row["window"] for row in points if row["status"] != "passed"), None),
            "rule": "largest filled point with correct initial answer and continuation; bounded bracket, not a global maximum",
            "points": points}


def execution_settings(cell, limit):
    settings = {"context_window_tokens": cell["window"],
                "max_output_tokens": 4096 if cell["kind"] == "context" else 100000}
    if cell["family"] == "splash":
        settings["max_memory_bytes"] = limit
    return settings


class SplashStudy:
    def __init__(self, arguments, package_root):
        self.args, self.package_root = arguments, Path(package_root)
        self.protocol = json.loads((self.package_root / "protocol.json").read_bytes())
        self.workloads = json.loads((self.package_root / "workloads.json").read_bytes())
        self.session = json.loads(Path(arguments.session).read_bytes())
        self.action = json.loads(Path(arguments.action).read_bytes())
        self.directory = Path(arguments.log_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cells = matrix(self.protocol, self.session["machine_facts"])
        self.rows = copy.deepcopy(self.session.get("answers", {}).get("completed-work", {}).get("value", {}).get("rows", []))
        self.safe_to_cleanup = self.session.get("protocol_evidence", {}).get("safe_to_cleanup", True)
        self.stopped = False
        self.limit = min(self.session["machine_facts"]["physical_memory_bytes"] * 3 // 4,
                         self.session["machine_facts"]["wired_limit_mib"] * 1024**2, 96 * 1024**3)
        package = json.loads((self.package_root / "package.json").read_bytes())
        definition = next(action for action in package["investigation"]["actions"] if action["id"] == self.action["id"])
        self.deadline = time.monotonic() + definition["runtime_minutes_max"] * 60 - 60

    def remaining(self, maximum):
        seconds = min(maximum, self.deadline - time.monotonic())
        if seconds <= 0:
            raise TimeoutError("approved action time exhausted")
        return seconds

    def command(self, arguments, timeout=120):
        result = run_process_silent([str(self.args.temper), *map(str, arguments)], self.remaining(timeout))
        if result.returncode:
            raise CommandFailure(arguments, result)
        return result.stdout

    def remove_installation(self, lock, installation):
        # A failed install can leave Temper's bounded operation lease. Retry
        # only that deterministic cleanup; never replay a model request.
        while True:
            try:
                self.command(["execution", "remove", "--lock", lock, "--root", self.args.root,
                              "--installation", installation], 600)
                return
            except CommandFailure as error:
                if "held by a live invocation" not in str(error):
                    raise
                time.sleep(self.remaining(15))

    def configure(self, cell, directory):
        model = cell["preset"]
        settings = execution_settings(cell, self.limit)
        lock = directory / "execution.lock.json"
        configured = configure_execution(self.args.temper, self.package_root / cell["lock"], model, settings, lock,
            runner=lambda argv, timeout: run_process_silent(argv, self.remaining(timeout)))
        execution = inspect_execution(Path(self.args.temper), lock,
            runner=lambda argv, timeout: run_process_silent(argv, self.remaining(timeout)))
        if execution["layouts"] != [model] or execution["profile"] != model:
            raise ProbeError("Temper configured a different preset")
        installation = self.args.installation if model == self.args.model else "qwen-study-" + model
        common = ["--lock", lock, "--root", self.args.root, "--installation", installation]
        started = time.monotonic()
        material = validate_material(self.command(["execution", "prepare", *common], self.protocol["prepare_seconds"]), execution)
        preparation_seconds = time.monotonic() - started
        paths = json.loads(self.command(["execution", "paths", *common], 600))
        if paths.get("schema") != "temper-execution-paths/v1" or paths.get("execution") != execution:
            raise ProbeError("installed paths are not bound to this execution")
        return {"lock": str(lock), "generation": material["generation"], "installation": installation,
                "settings": settings, "context_execution_sha256": configured["context_execution_sha256"],
                "paths": paths, "preparation_seconds": preparation_seconds}

    def probe(self, cell, material, directory):
        watch = copy.deepcopy(self.protocol["process_watch"])
        for key in ("rss_bytes_max", "current_footprint_bytes_max", "peak_footprint_bytes_max"):
            watch["roles"][0][key] = self.limit
        if cell["family"] in ("splash", "vllm-metal"):
            size = (2 if cell["family"] == "splash" else 4) * 1024**3
            watch["roles"].append({"id": "frontend", "rss_bytes_max": size,
                "current_footprint_bytes_max": size, "peak_footprint_bytes_max": size})
        if cell["family"] == "vllm-metal":
            watch["roles"].append({"id": "resource-tracker", "rss_bytes_max": 256 * 1024**2,
                "current_footprint_bytes_max": 256 * 1024**2, "peak_footprint_bytes_max": 256 * 1024**2})
        watch["roles"].sort(key=lambda role: role["id"])
        return ManagedProbe(temper=Path(self.args.temper), root=Path(self.args.root),
            installation=material["installation"], execution_lock=Path(material["lock"]),
            generation=material["generation"], listen=self.args.listen, log_dir=directory / "process",
            watch_spec=watch, router_ready_seconds=30, log_bytes_max=self.protocol["process_log_bytes_max"])

    def count(self, probe, cell, material, body, directory):
        def rapid(request):
            path = directory / "tokenizer-request.json"
            _atomic_write(path, canonical_json(request))
            command = [material["paths"]["python"]["rapid-mlx"], "-I", "-B", str(Path(__file__).with_name("rapid_tokenize.py")),
                       material["paths"]["models"][cell["preset"]], str(path)]
            result = subprocess.run(command, capture_output=True, timeout=self.remaining(180), check=False,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                     "RAPID_MLX_TELEMETRY": "0", "DO_NOT_TRACK": "1"})
            if result.returncode:
                raise ProbeError("Rapid tokenizer failed: " + result.stderr.decode(errors="replace")[-2048:])
            return json.loads(result.stdout.splitlines()[-1])["count"]
        return native_count(probe, cell["preset"], cell["family"], body, self.remaining(1800), rapid)

    def chat(self, probe, cell, material, case, directory, *, reserve=None, memory=None):
        body = request_body(case, cell["preset"], cell["window"], 1, cell["family"])
        count = self.count(probe, cell, material, body, directory)
        body = request_body(case, cell["preset"], cell["window"], count, cell["family"])
        if reserve is not None:
            if count + reserve > cell["window"]:
                raise ProbeError("context continuation exceeds the declared window")
            body["max_tokens"] = reserve
        if self.count(probe, cell, material, body, directory) != count:
            raise ProbeError("output allocation changed native input count")
        _atomic_write(directory / "request.json", canonical_json(body))
        stream = CodingStream(directory / "response.sse")
        failure = None
        try:
            response = monitored_call(probe, "/v1/chat/completions", body,
                self.remaining(self.protocol["context_request_seconds"] if reserve else self.protocol["request_seconds"]),
                streaming=True, stream_reader=stream, observer=memory.poll if memory else None)
        except (Exception, KeyboardInterrupt) as error:
            failure = {"kind": classify_failure(error), "message": str(error)}
            response = stream.snapshot()
            if isinstance(error, KeyboardInterrupt):
                self.stopped = True
        _atomic_write(directory / "response.json", canonical_json(response))
        response["reasoning_characters"] = len(response["message"].pop("reasoning"))
        response.update(performance(response, count))
        response.update(failure=failure, max_tokens=body["max_tokens"], id=case["id"])
        if failure or (response.get("usage") or {}).get("completion_tokens", 0) > body["max_tokens"]:
            response["measurement_valid"] = False
        return response

    def coding_case(self, probe, cell, material, case, directory, *, memory=None):
        response = self.chat(probe, cell, material, case, directory, memory=memory)
        response["status"] = "incomplete"
        if response["failure"] or not response["measurement_valid"]:
            return response
        if response["finish_reason"] == "length":
            response["status"] = "context-window-exhausted"
            return response
        try:
            arguments = submission(response)
            fixture = directory / "fixture"
            base = unpack(self.package_root / "flask.tar.gz", fixture)
            candidate = directory / "candidate"
            response["patch"] = apply(arguments, base, candidate, case["edit_scope"])
            response["evaluation"] = evaluate(candidate, directory / "evaluation", case, fixture,
                                              material["paths"]["python"]["coding-evaluator"])
            result = response["evaluation"]
            response["status"] = ("invalid-evaluation" if not result["valid"] else "tests-pass-needs-review" if result["all_pass"]
                else "candidate-test-failure" if result["behavior_pass"] else "behavior-failure")
        except InvalidPatch as error:
            response.update(status="invalid-submission", submission_error=str(error))
        except (Exception, KeyboardInterrupt) as error:
            # Preserve the delivered submission even when grading cannot finish.
            # Unverified grader shutdown has the same cleanup authority as an
            # unverified engine shutdown: retain the installation and stop.
            self.safe_to_cleanup = self.safe_to_cleanup and getattr(error, "safe_to_cleanup", True)
            self.stopped = True
            response.update(status="invalid-evaluation", evaluation_error=str(error))
        return response

    def context_cases(self, probe, cell, material, directory, *, memory=None):
        target = cell["window"] - 4096 - 1024
        def count(messages):
            body = request_body({"request": {"messages": messages}}, cell["preset"], cell["window"], 1)
            return self.count(probe, cell, material, body, directory)
        messages, expected, followup_expected = construct_context(target, count)
        results = []
        for identity, oracle in (("distributed-ledger", expected), ("ledger-followup", followup_expected)):
            stage = directory / identity
            stage.mkdir()
            row = self.chat(probe, cell, material, {"id": identity, "request": {"messages": messages}}, stage, reserve=4096, memory=memory)
            row.update(expected=oracle, correct=grade(row["message"]["content"], oracle)["correct"]
                       and row["finish_reason"] == "stop" and row["measurement_valid"])
            if identity == "distributed-ledger" and row["native_input_tokens"] != target:
                row["correct"] = False
            results.append(row)
            _atomic_write(directory / "context.json", canonical_json(results))
            if not row["correct"]:
                break
            messages += [{"role": "assistant", "content": row["message"]["content"]},
                         {"role": "user", "content": "From the original ledger, return only a JSON object mapping R02 and R04 to their values."}]
        return results

    def measure(self, cell):
        directory = self.directory / cell["id"]
        directory.mkdir()
        row = {"id": cell["id"], "preset": cell["preset"], "kind": cell["kind"], "window": cell["window"],
               "status": "unmeasured", "cases": [], "failure": None, "resources": [], "material": None}
        row["installation"] = self.args.installation if cell["preset"] == self.args.model else "qwen-study-" + cell["preset"]
        row["execution_lock"] = str(directory / "execution.lock.json")
        if cell["kind"] == "context":
            row["target_input_tokens"] = cell["window"] - 5120
        print("Measuring " + cell["id"] + " ...", flush=True)
        try:
            # Q8 remains a candidate. This is a necessary admission floor, not
            # a prediction that weights, caches and the task will fit.
            if cell.get("candidate"):
                minimum = cell["minimum_engine_memory_bytes"]
                if minimum > self.limit:
                    row.update(status="preflight-refused", failure={"kind": "memory-admission", "message": "weights plus 4 GiB minimum headroom exceed the approved engine budget"})
                    return row
            material = self.configure(cell, directory)
            row["material"] = {key: value for key, value in material.items() if key != "paths"}
            cases = ([case for case in self.workloads["cases"]
                      if "task_ids" not in cell or case["id"] in cell["task_ids"]]
                     if cell["kind"] == "coding" else [{"id": "context"}])
            for case in cases:
                stage = directory / case["id"]
                stage.mkdir()
                probe = self.probe(cell, material, stage)
                memory = None
                started = time.monotonic()
                try:
                    probe.start()
                    # Readiness starts the upstream model without a warm-up
                    # generation. Each coding task gets a fresh process.
                    endpoint = "/health/ready" if cell["family"] == "rapid-mlx" else "/health"
                    monitored_call(probe, "/upstream/" + cell["preset"] + endpoint, {}, self.remaining(1800), method="GET")
                    startup = time.monotonic() - started
                    spec = self.protocol.get("native_memory")
                    if cell["family"] == "splash" and spec:
                        source = "/upstream/" + cell["preset"] + "/status"
                        memory = SplashMemory(lambda: monitored_call(probe, source, {},
                            self.remaining(spec["timeout_seconds"]), method="GET"), source, spec["interval_seconds"])
                        memory.capture("loaded")
                    result = (self.coding_case(probe, cell, material, case, stage, memory=memory) if cell["kind"] == "coding"
                              else self.context_cases(probe, cell, material, stage, memory=memory))
                    returned = [result] if isinstance(result, dict) else result
                    for item in returned:
                        item["startup_seconds"] = startup
                    row["cases"].extend(returned)
                finally:
                    try:
                        if memory:
                            memory.capture("final")
                    except (KeyboardInterrupt, ProbeError):
                        self.stopped = True
                    finally:
                        resources = probe.finish()
                    if memory:
                        resources["native_memory"] = memory.result()
                    row["resources"].append(resources)
                    self.safe_to_cleanup = self.safe_to_cleanup and resources.get("safe_to_cleanup", False)
                    if not self.safe_to_cleanup:
                        self.stopped = True
                    if (resources.get("stop_reasons") or resources.get("thermal_warning_observed")
                            or resources.get("cpu_speed_limit_max", 0)
                            or resources.get("max_gap_milliseconds", 0) > 15000
                            or resources.get("max_sleep_wake_milliseconds", 0) > 15000):
                        self.stopped = True
                    if resources.get("issues") or not resources.get("samples"):
                        for item in row["cases"]:
                            item["measurement_valid"] = False
                            if "correct" in item:
                                item["correct"] = False
                    _atomic_write(directory / "observation.json", canonical_json(row))
                if self.stopped or any(item.get("failure") for item in row["cases"]):
                    break
            if cell["kind"] == "context":
                row["status"] = "passed" if len(row["cases"]) == 2 and all(item["correct"] for item in row["cases"]) else "unsuccessful"
            else:
                row["status"] = "measured" if len(row["cases"]) == len(cases) and all(item["measurement_valid"] for item in row["cases"]) else "incomplete"
        except (Exception, KeyboardInterrupt) as error:
            row.update(status="failed", failure={"kind": classify_failure(error), "message": str(error)})
            if isinstance(error, KeyboardInterrupt):
                self.stopped = True
        finally:
            _atomic_write(directory / "observation.json", canonical_json(row))
        return row

    def answers(self):
        return {
            "completed-work": {"state": "observed", "value": {"rows": self.rows}, "reason": "first attempts with frozen test groups; passing tests still requires source review"},
            "context": {"state": "observed" if any(row["kind"] == "context" for row in self.rows) else "unknown", "value": context_summary(self.rows), "reason": "only the 36 GiB route runs filled context points; unattempted points remain unknown"},
            "fit": {"state": "observed", "value": {"engine_memory_limit_bytes": self.limit}, "reason": "each row retains startup, resource observations and failures; no bucket extrapolation"},
            "interaction": {"state": "observed", "value": {"rule": "per-task latency and native rates in completed-work; no mixing engines or quantizations"}},
            "profile": {"state": "observed", "value": {"bucket": bucket(self.session["machine_facts"]), "planned_cells": [cell["id"] for cell in self.cells]}},
            "tuning": {"state": "unknown", "reason": "this study compares frozen compositions; it does not search engine flags"},
            "limits": {"state": "observed", "value": {"unattempted_cells": [cell["id"] for cell in self.cells if cell["id"] not in {row["id"] for row in self.rows}],
                "storage_cold": "unproven; artifact verification can warm file caches", "comparison": "GGUF/Frog with Splash or llama; shared MLX/native template with Rapid or vLLM",
                "quality": "frozen tests and retained patch require review; existing async cleanup caveat remains outside the frozen oracle"}},
        }

    def run(self):
        identity = self.action["id"]
        if identity == "finish-study":
            # Remove each extra software installation through its owner before
            # the shared workflow removes the initial installation and root.
            if not self.safe_to_cleanup:
                raise ProbeError("owned shutdown is incomplete")
            seen = set()
            for row in self.rows:
                installation = row["installation"]
                if installation != self.args.installation and installation not in seen and Path(row["execution_lock"]).is_file():
                    self.remove_installation(row["execution_lock"], installation)
                    seen.add(installation)
            next_actions = []
        else:
            expected = next_cell(self.cells, self.rows)
            if expected is None or identity != expected["id"]:
                raise ProbeError("action is outside the next approved matrix cell")
            self.rows.append(self.measure(expected))
            following = next_cell(self.cells, self.rows)
            next_actions = [{"id": "finish-study", "parameters": {}}]
            if self.safe_to_cleanup:
                next_actions = [{"id": following["id"] if following and not self.stopped else "finish-study", "parameters": {}}]
        return {"schema": "field-kit-action-result/v3", "status": "complete", "session_id": self.session["id"],
                "action": {"id": identity, "attempt": self.action["attempt"]}, "answers": self.answers(), "next_actions": next_actions,
                "protocol": {"schema": self.protocol["schema"], "status": "complete", "model": self.args.model, "generation": self.args.generation,
                             "safe_to_cleanup": self.safe_to_cleanup}}


def main(package_root, *, study_type=SplashStudy):
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("study interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    for name in ("action", "temper", "root", "execution-lock", "generation", "installation", "model", "listen", "report", "log-dir", "field-kit-runtime", "session", "outcome"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    result = study_type(args, package_root).run()
    _atomic_write(Path(args.report), canonical_json(result))
    return 0
