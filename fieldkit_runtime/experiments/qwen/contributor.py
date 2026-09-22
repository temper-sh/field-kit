"""A contributor can run the study without learning the maintainer commands."""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ...catalog import QuestionCatalog, Refusal, canonical_json, digest, parse_machine_facts
from .method import SELECTOR
from ...workflow import (Workflow, _atomic_write, _exclusive_session_lock, build_export, load_session,
                       run_contributor_process)


def read_machine_facts(temper):
    try:
        completed = subprocess.run(
            [str(temper), "machine", "facts"], capture_output=True, check=False,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise Refusal("Temper machine facts timed out after 30 seconds") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        raise Refusal("Temper could not read machine facts" + (f": {detail}" if detail else ""))
    return parse_machine_facts(completed.stdout), completed.stdout


def confirm(message, *, input_fn=input):
    try:
        return input_fn(message).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def format_report(session):
    answers = session.get("answers", {})
    machine = session["machine_facts"]
    performance = answers.get("interaction", {}).get("value", {})
    tuning = answers.get("tuning", {}).get("value", {})
    def value(item):
        return "unmeasured" if item is None else str(round(item, 2)) if isinstance(item, float) else str(item)
    lines = ["# Qwen machine study", "", f"{machine['chip']} · {machine['physical_memory_bytes'] / 1024**3:g} GiB · macOS {machine['target'].get('distribution_version', 'unknown')}",
             "", f"Run: {session['id']} · started {session['started_at']}", "",
             "## Common baseline", "", "| Measurement | Observed |", "|---|---:|"]
    for label, key in (("Prefill, tokens/s (median)", "prefill_tokens_per_second_median"),
                       ("Generation, tokens/s (median)", "generation_tokens_per_second_median"),
                       ("Total request time, seconds", "total_service_seconds"),
                       ("Correct tasks", "correct_cases"), ("Measured tasks", "measured_cases")):
        lines.append(f"| {label} | {value(performance.get(key))} |")
    rss = performance.get("peak_engine_rss_bytes")
    lines += [f"| Peak engine RSS, GiB | {value(rss / 1024**3 if rss is not None else None)} |", "",
              f"Measurement status: {answers.get('interaction', {}).get('state', 'unknown')}. {performance.get('summary_rule', '')}", "",
              "| Scenario | Total seconds | First token | First answer | Correct |",
              "|---|---:|---:|---:|---|"]
    for row in performance.get("scenarios", []):
        lines.append(f"| {row['id']} | {value(row.get('service_seconds'))} | {value(row.get('first_token_seconds'))} | {value(row.get('first_answer_seconds'))} | {row.get('correct')} |")
    settings = (answers.get("profile", {}).get("value", {}).get("baseline") or {}).get("settings", {})
    if settings:
        engine = settings["engine_config"]
        lines += ["", f"Baseline: {settings['engine']} · {settings['context_window_tokens']:,} context · {engine['kv_cache']} K/V · batch/microbatch {engine['batch_tokens']}/{engine['microbatch_tokens']} · {engine['context_checkpoints']} checkpoints · {engine['prompt_cache_ram_mib']} MiB extra cache."]
    lines += ["",
              "## Tuning", "", f"Flag selection: **{tuning.get('decision', {}).get('selection', 'unresolved')}**.",
              tuning.get("decision", {}).get("reason", "Tuning was not run."), "",
              "| KV cache | Highest successful input | Window within the study latency budget |", "|---|---:|---:|"]
    for cache, summary in answers.get("context", {}).get("value", {}).get("summary", {}).items():
        lines.append(f"| {cache} | {value(summary.get('highest_witnessed_input_tokens'))} | {value(summary.get('within_budget_window_tokens'))} |")
    lines += ["", "Context budget: first answer completed within 300 seconds; follow-up within 60 seconds. Each point reserves 4,096 output tokens. These are declared study defaults, not universal responsiveness requirements.",
              "", "Context tests use baseline flags with the named cache precision. A flag candidate and a larger context have not been tested together unless explicitly recorded.",
              "", "## Evidence and limits", "", "Exact configurations, per-request latency, token counts, answers, memory observations, failures and unattempted work are in the accompanying JSON. Results describe this Mac; bucket recommendations require review across machines. Raw speed does not establish broad task quality.",
              "", "Nothing was uploaded. The JSON can contain generated answers, machine facts and local paths. Review it before sharing.", ""]
    limits = answers.get("limits", {}).get("value", {})
    for key in ("baseline_failure", "tuning_stop"):
        if limits.get(key):
            lines += [f"{key}: {limits[key]}", ""]
    return "\n".join(lines)


def write_results(session_path, destination):
    session = load_session(session_path)
    packet, _ = build_export(session_path)
    if destination.is_symlink():
        raise Refusal("result directory must not be a symlink")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, data in (("result.json", packet), ("report.md", format_report(session).encode())):
        path = destination / name
        if path.is_symlink():
            raise Refusal("result file must not be a symlink")
        _atomic_write(path, data)
    print(f"\nReport: {destination / 'report.md'}")
    print(f"Result to review and send back: {destination / 'result.json'}")
    print("Nothing was uploaded. The result includes generated answers, machine facts and local paths.")


def continue_study(workflow, session_path, tuning):
    session = workflow.resume(session_path)
    if session["state"] not in ("awaiting-action", "ready-to-finish", "complete"):
        session = workflow.run(session_path, lambda: True)
    if session["state"] == "awaiting-action":
        if session.get("protocol_evidence", {}).get("safe_to_cleanup") is not True:
            raise Refusal("owned shutdown was not established; the installation and observations have been retained")
        action = {"id": "tune-machine", "parameters": {"scope": tuning}}
        if tuning != "none" and action in session.get("allowed_actions", []):
            session = workflow.submit_action(session_path, {**action,
                "reason": "Execute the tuning scope selected in the contributor plan."})
        finish = {"id": "finish-study", "parameters": {}}
        if finish not in session.get("allowed_actions", []):
            raise Refusal("owned shutdown was not established; the installation and observations have been retained")
        session = workflow.submit_action(session_path, {**finish,
            "reason": "Consolidate all observations, including failures and unmeasured points."})
    if session["state"] == "ready-to-finish":
        session = workflow.finish(session_path, lambda: True)
    if session["state"] == "complete" and session.get("cleanup") != "complete":
        session = workflow.run(session_path, lambda: True)
    return session


def contribute(arguments, repository, *, input_fn=input, facts_reader=None, runner=run_contributor_process):
    local = repository / ".local"
    if not local.is_dir() or local.is_symlink():
        raise Refusal("run ./setup.sh first; it installs the signed runtime locally")
    if arguments.preview:
        return _contribute_unlocked(arguments, repository, input_fn=input_fn, facts_reader=facts_reader, runner=runner)
    # Prevent a double invocation from creating two model downloads or replacing
    # the pointer to an unfinished study. The lock is released on process death.
    lock = local / "contributor.lock"
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(descriptor)
    with _exclusive_session_lock(lock):
        return _contribute_unlocked(arguments, repository, input_fn=input_fn, facts_reader=facts_reader, runner=runner)


def _contribute_unlocked(arguments, repository, *, input_fn=input, facts_reader=None, runner=run_contributor_process):
    facts_reader = facts_reader or read_machine_facts
    local = repository / ".local"
    temper = arguments.temper or local / "temper"
    if not temper.is_file() or not local.is_dir() or local.is_symlink():
        raise Refusal("run ./setup.sh first; it installs the signed runtime locally")
    catalog = QuestionCatalog.load(repository / "catalog/questions.json")
    entry = catalog.find(SELECTOR)
    pointer = local / "qwen-study-2-session.json"
    if not arguments.new and not pointer.exists() and (local / "contributor-session.json").exists():
        raise Refusal("This clone has a revision 1 study. Resume or review it with its original source revision; use --new only to start a separate revision 2 study.")
    metadata = None
    if pointer.is_symlink():
        raise Refusal("contributor session pointer must not be a symlink")
    if pointer.is_file() and not arguments.new:
        metadata = json.loads(pointer.read_bytes())
        if digest(canonical_json(metadata["consent"])) != metadata["sha256"]:
            raise Refusal("contributor consent record changed")
        metadata = metadata["consent"]
        session_path = Path(metadata["session"])
        if session_path.parent != local.resolve():
            raise Refusal("contributor session is outside this clone")
        session = load_session(session_path)
        if session["plan"]["sha256"] != metadata["plan_sha256"]:
            raise Refusal("contributor consent differs from the session plan")
        destination = repository / "runs" / session_path.name.removesuffix(".session.json")
        if session["state"] == "complete" and session.get("cleanup") == "complete":
            if not arguments.preview:
                write_results(session_path, destination)
            print("This run is complete. Use ./field-kit contribute --new for another measurement.")
            return 0
        if arguments.preview:
            print(f"Unfinished study: {session_path}. Run ./field-kit to resume the same approved plan.")
            return 0
    facts, raw = facts_reader(temper)
    applicable, reasons = entry.applicable(facts)
    if not applicable:
        raise Refusal("this Qwen study cannot run here: " + "; ".join(reasons)
                      + "\nSee docs/experiments/qwen-machine-study.md for requirements "
                        "and wired-memory setup (Adjust the wired-memory limit).")
    stages = {"prepare-execution": "Installing and verifying the exact study configuration (about 17.6 GB of model data)",
              "field-kit-outcome": "Removing the experiment installation"}
    def progress(message):
        for key, label in stages.items():
            if f"stage={key} " in message:
                print(label + " ...", flush=True)
                return
    workflow = Workflow(entry, facts, raw, temper, runner=runner, progress=progress)
    if metadata:
        print("Resuming the same approved study; completed setup and measurements are preserved.")
        tuning = metadata["tuning"]
    else:
        tuning = arguments.tuning
        if tuning is None:
            tuning = "both"
            if not arguments.preview:
                print("1. Performance and tuning (up to 45 minutes + 4 hours of model work)")
                print("2. Performance only (up to 45 minutes of model work)")
                try:
                    choice = input_fn("Choose [1]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return 0
                if choice not in ("", "1", "2"):
                    raise Refusal("choose 1 or 2; nothing was started")
                tuning = "none" if choice == "2" else "both"
        identity = "qwen-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        root = local.resolve() / identity
        plan = workflow.plan(root, "restore")
        available = shutil.disk_usage(local).free
        required = entry.package["cost"]["temporary_disk_bytes_max"]
        if available < required:
            raise Refusal(f"the study needs {required / 1024**3:.1f} GiB free; this volume has {available / 1024**3:.1f} GiB")
        machine = facts.document
        print(f"\nQwen3.8 27B · {machine['chip']} · {machine['physical_memory_bytes'] / 1024**3:g} GiB")
        print(f"Download: up to {entry.package['cost']['network_bytes_max'] / 10**9:.1f} GB, once for the study. Disk: up to {required / 1024**3:.1f} GiB.")
        print("Time ceiling: 45 minutes of baseline work" + (" + 4 hours of tuning." if tuning != "none" else "."))
        print("Setup has a separate 6-hour download/install ceiling. These are maximums, not duration estimates.")
        memory_limit = min(machine["physical_memory_bytes"] * 3 // 4, machine["wired_limit_mib"] * 1024**2, 96 * 1024**3)
        print(f"Engine memory limit: {memory_limit / 1024**3:g} GiB; router limit: 2 GiB. Stop at 512 MiB additional swap or thermal/observation limits.")
        print("Keep the Mac awake and connected to power.")
        print("It uses a dedicated local installation and removes it afterward. Existing AI services and system settings are unchanged.")
        print("Results stay in this clone and include model answers, machine facts and local paths. Nothing is uploaded.")
        print("Ctrl-C requests a graceful stop and saves observations. Allow cleanup to finish.")
        if arguments.preview:
            print("Preview only; no files, downloads or model processes were created.")
            return 0
        if not confirm("Start this study? [y/N]: ", input_fn=input_fn):
            print("Nothing started.")
            return 0
        _, session_path = workflow.start(plan, plan.sha256)
        metadata = {"session": str(session_path), "plan_sha256": plan.sha256, "tuning": tuning}
        _atomic_write(pointer, canonical_json({"consent": metadata, "sha256": digest(canonical_json(metadata))}))
        destination = repository / "runs" / identity
    try:
        continue_study(workflow, session_path, tuning)
    except (Exception, KeyboardInterrupt):
        print(f"\nStudy retained at {session_path}. Run ./field-kit to inspect or resume it; first measurements are never silently repeated.", file=sys.stderr)
        raise
    write_results(session_path, destination)
    return 0
