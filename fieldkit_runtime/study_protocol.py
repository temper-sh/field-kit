"""Execute the bounded Qwen study using one Temper-owned installation."""
from __future__ import annotations

import argparse
import copy
import json
import signal
import time
from pathlib import Path

from .catalog import canonical_json, digest
from .execution import prepare_execution
from .measurement import construct_context, measure_chat, monitored_call
from .probe import ManagedProbe, ProbeError
from .study import (FIELDS, FLAG_VARIANTS, SCHEMA, choose_candidate, confirm_candidate,
                    context_summary, performance_summary, valid_work, variant_records)
from .workflow import _atomic_write, _parse_generation, run_process_silent, CommandFailure


class Study:
    def __init__(self, arguments, package_root: Path):
        self.arguments = arguments
        self.package_root = package_root
        self.protocol = json.loads((package_root / "protocol.json").read_bytes())
        self.workloads = json.loads((package_root / "workloads.json").read_bytes())
        self.lock = json.loads(Path(arguments.execution_lock).read_bytes())
        self.action = json.loads(Path(arguments.action).read_bytes())
        self.session = json.loads(Path(arguments.session).read_bytes())
        self.directory = Path(arguments.log_dir)
        self.model = arguments.model
        self.runs = []
        self.points = []
        self.safe_to_cleanup = True
        self.stopped = False
        self.stop_reason = None
        minutes = self.protocol["baseline_minutes"] if self.action["id"] == "measure-baseline" else self.protocol["tuning_minutes"]
        self.deadline = time.monotonic() + minutes * 60 - 120

    def remaining(self, maximum=1800):
        value = min(maximum, self.deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError("approved study time budget exhausted")
        return value

    def command(self, arguments, timeout=300):
        result = run_process_silent([str(self.arguments.temper), *map(str, arguments)], self.remaining(timeout))
        if result.returncode:
            raise CommandFailure(arguments, result)
        return result.stdout

    def configure(self, directory, variant, *, cache=None, window=None):
        records = variant_records(self.lock, self.model, variant, cache=cache, window=window)
        catalog, selection, lock_path = directory / "catalog.json", directory / "selection.json", directory / "execution.lock.json"
        _atomic_write(catalog, canonical_json(records))
        _atomic_write(selection, canonical_json(self.lock["selection"]))
        self.command(["catalog", "compile", "--catalog", catalog, "--selection", selection,
                      "--target", "darwin/arm64", "--out", lock_path, "--json"], 60)
        exported = prepare_execution(Path(self.arguments.temper), lock_path, directory / "inputs",
            runner=lambda argv, timeout: run_process_silent(argv, self.remaining(timeout)))
        compiled = json.loads(lock_path.read_bytes())
        if compiled["records"] != records or compiled["selection"] != self.lock["selection"]:
            raise ProbeError("Temper compiled settings outside the approved configuration")
        inputs = directory / "inputs"
        manifest, manifest_lock = inputs / "manifest.yaml", inputs / "manifest.lock.yaml"
        if manifest_lock.read_bytes() != Path(self.arguments.manifest_lock).read_bytes():
            raise ProbeError("tuning changed the pinned model or template supply")
        generation = _parse_generation(self.command(["apply", "--root", self.arguments.root,
            "--manifest", manifest, "--lock", manifest_lock, "--mode", exported["profile"]]))
        self.command(["check", "--root", self.arguments.root, "--manifest", manifest,
                      "--lock", manifest_lock, "--mode", exported["profile"], "--verify"])
        binding = self.command(["field-kit", "bind", "--root", self.arguments.root,
            "--manifest-lock", manifest_lock, "--generation", generation,
            "--installation", self.arguments.installation + "=" + self.arguments.software_lock], 60)
        _atomic_write(directory / "binding.yaml", binding)
        return {"generation": generation, "execution_lock_sha256": digest(lock_path.read_bytes()),
                "execution_digest": exported["execution_digest"], "settings": records["layouts"][self.model],
                "binding_sha256": digest(binding)}

    def watch_spec(self):
        spec = copy.deepcopy(self.protocol["process_watch"])
        facts = self.session["machine_facts"]
        limit = min(int(facts["physical_memory_bytes"] * 3 // 4), facts["wired_limit_mib"] * 1024**2,
                    self.protocol["engine_memory_bytes_max"])
        for key in ("rss_bytes_max", "current_footprint_bytes_max", "peak_footprint_bytes_max"):
            spec["roles"][0][key] = limit
        return spec

    def probe(self, directory, material):
        return ManagedProbe(temper=Path(self.arguments.temper), root=Path(self.arguments.root),
            installation=self.arguments.installation, software_lock=Path(self.arguments.software_lock),
            generation=material["generation"], listen=self.arguments.listen, log_dir=directory / "process",
            watch_spec=self.watch_spec(), router_ready_seconds=30, log_bytes_max=self.protocol["process_log_bytes_max"])

    def capture(self, variant, operation, *, cache=None, window=None):
        """Preserve partial answers even when a later request or resource check fails."""
        index = len(self.runs) + len(self.points)
        identity = variant if cache is None else f"{cache}-{window}"
        directory = self.directory / f"configuration-{index:02d}-{identity}"
        result = {"configuration": identity, "cases": [], "valid": False, "correct": False,
                  "failure": None, "resources": {}, "material": None, "attempted_case_ids": []}
        probe = None
        print(f"Measuring {identity} ...", flush=True)
        try:
            self.remaining()
            result["material"] = self.configure(directory, variant, cache=cache, window=window)
            probe = self.probe(directory, result["material"])
            probe.start()
            operation(probe, result, directory)
            result["valid"] = True
            result["correct"] = bool(result["cases"]) and all(row["correct"] for row in result["cases"])
        except (Exception, KeyboardInterrupt) as error:
            result["failure"] = {"kind": "interrupted" if isinstance(error, KeyboardInterrupt) else "invalid-measurement", "message": str(error)}
            self.stopped, self.stop_reason = True, result["failure"]
        finally:
            if probe is not None:
                result["resources"] = probe.finish()
                self.safe_to_cleanup = self.safe_to_cleanup and result["resources"].get("safe_to_cleanup", False)
                if result["resources"].get("issues") or not result["resources"].get("samples"):
                    result["valid"] = False
                    self.stopped = True
                    result["failure"] = result["failure"] or {"kind": "invalid-observation", "message": "resource observation or shutdown incomplete"}
                    self.stop_reason = result["failure"]
            _atomic_write(directory / "observation.json", canonical_json(result))
        return result

    def measure_workload(self, variant):
        def run(probe, result, directory):
            histories = {}
            layout = result["material"]["settings"]
            for case in self.workloads["cases"]:
                print(f"  {case['id']} ...", flush=True)
                messages = copy.deepcopy(histories.get(case.get("from"), []))
                messages.append({"role": "user", "content": case["prompt"]})
                result["attempted_case_ids"].append(case["id"])
                _atomic_write(directory / "observation.json", canonical_json(result))
                row = measure_chat(probe, self.model, messages, case["expected"], layout["context_window_tokens"],
                                   layout["request_defaults"]["max_output_tokens"], self.remaining(self.protocol["request_seconds"]))
                row.update(id=case["id"], transition=case["transition"], request_sha256=digest(canonical_json(messages)))
                result["cases"].append(row)
                histories[case["id"]] = messages + [{"role": "assistant", "content": row["content"]}]
                _atomic_write(directory / "observations.json", canonical_json(result["cases"]))
                print(f"  {case['id']}: {row['service_seconds']:.1f}s, " + ("correct" if row["correct"] else "incorrect or incomplete"), flush=True)
                if not row["measurement_valid"]:
                    raise ProbeError("; ".join(row["measurement_problems"]))
        result = self.capture(variant, run)
        self.runs.append(result)
        return result

    def measure_context(self, cache, window):
        target = window - self.protocol["output_reserve_tokens"] - self.protocol["followup_reserve_tokens"]

        def run(probe, result, directory):
            def count(messages):
                rendered = monitored_call(probe, "/apply-template", {"model": self.model, "messages": messages}, self.remaining(120))
                if not isinstance(rendered, dict) or not isinstance(rendered.get("prompt"), str):
                    raise ProbeError("native template endpoint did not return a prompt")
                tokenized = monitored_call(probe, "/tokenize", {"model": self.model, "content": rendered["prompt"],
                    "add_special": True, "parse_special": True, "with_pieces": False}, self.remaining(120))
                tokens = tokenized.get("tokens")
                if not isinstance(tokens, list) or any(type(token) is not int or token < 0 for token in tokens):
                    raise ProbeError("native tokenizer returned no token IDs")
                return len(tokens)

            messages, expected, followup_expected = construct_context(target, count)
            reserve = self.protocol["output_reserve_tokens"]
            result["attempted_case_ids"].append("distributed-ledger")
            _atomic_write(directory / "observation.json", canonical_json(result))
            primary = measure_chat(probe, self.model, messages, expected, window, reserve, self.remaining(self.protocol["context_request_seconds"]))
            primary.update(id="distributed-ledger", expected=expected, request_sha256=digest(canonical_json(messages)))
            result["cases"].append(primary)
            _atomic_write(directory / "observations.json", canonical_json(result["cases"]))
            if not primary["measurement_valid"]:
                raise ProbeError("; ".join(primary["measurement_problems"]))
            if primary["usage"]["prompt_tokens"] != target:
                raise ProbeError("actual chat input differs from the native preflight token count")
            messages += [{"role": "assistant", "content": primary["content"]},
                         {"role": "user", "content": "From the original ledger, return only a JSON object mapping R02 and R04 to their values."}]
            if count(messages) + reserve > window:
                raise ProbeError("actual follow-up leaves insufficient output headroom")
            result["attempted_case_ids"].append("ledger-followup")
            _atomic_write(directory / "observation.json", canonical_json(result))
            followup = measure_chat(probe, self.model, messages, followup_expected, window, reserve,
                                    self.remaining(self.protocol["context_request_seconds"]))
            followup.update(id="ledger-followup", expected=followup_expected, request_sha256=digest(canonical_json(messages)))
            result["cases"].append(followup)
            if not followup["measurement_valid"]:
                raise ProbeError("; ".join(followup["measurement_problems"]))
            result["input_tokens"] = primary["usage"]["prompt_tokens"]

        result = self.capture("context", run, cache=cache, window=window)
        cases = result["cases"]
        result.update(cache=cache, window=window, target_input_tokens=target,
                      input_tokens=result.get("input_tokens", (cases[0].get("usage") or {}).get("prompt_tokens") if cases else None),
                      initial_seconds=cases[0]["service_seconds"] if cases else None,
                      followup_seconds=cases[1]["service_seconds"] if len(cases) == 2 else None)
        self.points.append(result)
        return result

    def tune(self, baseline, scope):
        screens, confirmation = [], []
        decision = {"selection": "unresolved", "reason": "flag tuning was not requested"}
        if scope in ("both", "flags"):
            for variant in FLAG_VARIANTS:
                if self.stopped:
                    break
                screens.append(self.measure_workload(variant))
            candidate = choose_candidate(baseline, screens, self.protocol["worthwhile_improvement_percent"])
            if candidate and not self.stopped:
                for variant in ("baseline", candidate, candidate, "baseline"):
                    if self.stopped:
                        break
                    confirmation.append(self.measure_workload(variant))
                decision = confirm_candidate(confirmation, self.protocol["worthwhile_improvement_percent"], self.protocol["maximum_case_regression_percent"])
            elif valid_work(baseline) and len(screens) == len(FLAG_VARIANTS) and all(run["valid"] for run in screens):
                decision = {"selection": "baseline", "reason": "no screened alternative justified confirmation under the declared improvement rule"}
            else:
                decision = {"selection": "unresolved", "reason": "screening or the baseline is incomplete or invalid"}
        if scope in ("both", "context"):
            stopped_cache = set()
            for window in self.protocol["context_windows"]:
                for cache in ("q8", "q4"):
                    if self.stopped:
                        break
                    if cache in stopped_cache:
                        continue
                    point = self.measure_context(cache, window)
                    if not point["valid"] or not point["correct"]:
                        stopped_cache.add(cache)
                if self.stopped:
                    break
        return {"scope": scope, "screens": screens, "confirmation": confirmation, "decision": decision,
                "context_points": self.points, "context_summary": context_summary(self.points,
                    self.protocol["useful_initial_seconds"], self.protocol["useful_followup_seconds"]),
                "stop": self.stop_reason}

    def answers(self, baseline, tuning=None):
        tuning = tuning or {"decision": {"selection": "unresolved", "reason": "tuning not run"}, "context_points": []}
        completed = "observed" if baseline["valid"] and baseline["correct"] else "failed" if baseline["valid"] else "unknown"
        return {
            "completed-work": {"state": completed, "value": {"cases": baseline["cases"], "completed": sum(row["correct"] for row in baseline["cases"]), "total": len(self.workloads["cases"])}, "reason": "frozen first-attempt artifact checks"},
            "context": {"state": "observed" if tuning.get("context_points") else "unknown", "value": {"points": tuning.get("context_points", []), "summary": tuning.get("context_summary", {}), "output_reserve_tokens": self.protocol["output_reserve_tokens"]}, "reason": "observed context points only; unmeasured lengths remain unknown"},
            "fit": {"state": "observed" if baseline["valid"] else "unknown", "value": baseline["resources"], "reason": "baseline resource observation; each tuning run retains its own memory measurements"},
            "interaction": {"state": "observed" if baseline["valid"] else "unknown", "value": performance_summary(baseline), "reason": "common baseline performance; configurations are never blended"},
            "profile": {"state": "observed", "value": {"baseline": baseline["material"], "source_lock_sha256": digest(Path(self.arguments.execution_lock).read_bytes()), "request_overrides": ["stream", "stream_options.include_usage", "timings_per_token"]}},
            "limits": {"state": "observed", "value": {"baseline_failure": baseline["failure"], "tuning_stop": tuning.get("stop"),
                "baseline_attempted_case_ids": baseline["attempted_case_ids"],
                "incomplete_baseline_cases": [identity for identity in baseline["attempted_case_ids"] if identity not in {row["id"] for row in baseline["cases"]}],
                "unattempted_baseline_cases": [case["id"] for case in self.workloads["cases"] if case["id"] not in baseline["attempted_case_ids"]],
                "storage_cold": "unproven; preceding verification can warm file caches", "settings": "compiled and bound through Temper; full native per-request settings observation is not supplied", "scope": "exact witnessed machine; no hardware bucket extrapolation, broad capability or human-time-saved claim"}},
            "tuning": {"state": "observed" if tuning.get("screens") or tuning.get("context_points") else "unknown", "value": tuning, "reason": "bounded comparisons and context points; candidate flags and context recommendations remain separate configurations"},
        }

    def run(self):
        identity = self.action["id"]
        if identity == "measure-baseline":
            baseline = self.measure_workload("baseline")
            answers = self.answers(baseline)
            next_actions = [{"id": "finish-study", "parameters": {}}]
            if baseline["valid"] and self.safe_to_cleanup:
                next_actions += [{"id": "tune-machine", "parameters": {"scope": scope}} for scope in ("both", "context", "flags")]
        elif identity == "tune-machine":
            prior = self.session["answers"]
            baseline = {"configuration": "baseline", "valid": prior["interaction"]["state"] == "observed",
                "correct": prior["completed-work"]["state"] == "observed", "cases": prior["completed-work"]["value"]["cases"],
                "material": prior["profile"]["value"]["baseline"], "resources": prior["fit"].get("value", {}),
                "failure": prior["limits"]["value"]["baseline_failure"],
                "attempted_case_ids": prior["limits"]["value"]["baseline_attempted_case_ids"]}
            tuning = self.tune(baseline, self.action["parameters"]["scope"])
            answers = self.answers(baseline, tuning)
            next_actions = [{"id": "finish-study", "parameters": {}}]
        elif identity == "finish-study":
            answers = self.session["answers"]
            self.safe_to_cleanup = self.session.get("protocol_evidence", {}).get("safe_to_cleanup") is True
            next_actions = []
        else:
            raise ProbeError("unknown study action")
        return {"schema": "field-kit-action-step-result/v1", "status": "complete",
                "action_sha256": digest(Path(self.arguments.action).read_bytes()),
                "step_sha256": digest(Path(self.arguments.step).read_bytes()),
                "outcome": "terminal", "consumed_artifacts": {}, "produced_artifacts": {},
                "answers": answers, "next_actions": next_actions,
                "protocol": {"schema": SCHEMA, "status": "complete", "model": self.model,
                    "generation": self.arguments.generation, "generation_scope": "initial approved installation; each measured configuration binds its own recorded generation",
                    "safe_to_cleanup": self.safe_to_cleanup}}


def main(package_root: Path):
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("study interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    for name in ("action", "step", "temper", "root", "software-lock", "execution-lock", "request-defaults", "manifest-lock", "generation", "installation", "model", "listen", "report", "log-dir", "field-kit-runtime", "session", "outcome"):
        parser.add_argument("--" + name, required=True)
    arguments = parser.parse_args()
    study = Study(arguments, package_root)
    result = study.run()
    _atomic_write(Path(arguments.report), canonical_json(result))
    return 0
