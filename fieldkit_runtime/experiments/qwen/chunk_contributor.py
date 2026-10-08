"""Consent to one configuration, checkpoint each task, clean up, and stop."""
from __future__ import annotations

import datetime
from functools import partial
import json
import os
from pathlib import Path
import shutil

from ...catalog import QuestionCatalog, Refusal
from ...execution import configure_execution
from ...planner import load_plan
from ...workflow import Workflow, _atomic_write, _exclusive_session_lock, load_session, run_contributor_process
from .chunks import SELECTOR, available_configurations, configuration_from_selector, select_package, selected_cells
from .contributor import confirm, read_machine_facts
from .splash_contributor import check_machine, format_report, write_results
from .splash_study import execution_settings


def retained_runs(local, source):
    """Derive progress from the actual sessions; no second progress registry."""
    runs = []
    for path in sorted(local.glob("qwen-5-*.session.json")):
        session = load_session(path)
        identity = configuration_from_selector(session["package"]["selector"])
        selected = select_package(source, identity)
        plan = load_plan(Path(session["plan"]["path"]))
        if (session["package"]["sha256"] != selected.package_sha256
                or plan.sha256 != session["plan"]["sha256"]
                or plan.document["inputs"]["package"]["sha256"] != selected.package_sha256
                or session["paths"]["session"] != str(path)
                or Path(session["paths"]["root"]).parent != local):
            raise Refusal(f"retained configuration does not match its consented inputs: {path}")
        runs.append((path, session, identity))
    return runs


def show_next(protocol, facts, runs):
    attempted = {identity for _, _, identity in runs}
    remaining = [item["id"] for item in available_configurations(protocol, facts) if item["id"] not in attempted]
    if remaining:
        print("Remaining configurations (comparisons using the same weights are adjacent): " + ", ".join(remaining))
        print("Choose explicitly with ./field-kit contribute --configuration NAME, or --next for " + remaining[0] + ".")
    else:
        print("All applicable configurations have retained runs. --new explicitly starts a separate measurement.")


def write_checkpoint(session, repository, path):
    destination = repository / "runs" / path.name.removesuffix(".session.json")
    if destination.is_symlink():
        raise Refusal("report directory must not be a symlink")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = destination / "report.md"
    if report_path.is_symlink():
        raise Refusal("report must not be a symlink")
    report = format_report(session) + "\nRun state: " + session["state"] + ". Task checkpoints are retained in the session.\n"
    _atomic_write(report_path, report.encode())
    print(f"Checkpoint saved: {report_path}")


def continue_run(workflow, path, repository, *, pause_after_task=False):
    session = workflow.resume(path)
    measured = False
    if session["state"] not in ("awaiting-action", "ready-to-finish", "complete"):
        session = workflow.run(path, lambda: True)
        measured = True
        write_checkpoint(session, repository, path)
    while session["state"] == "awaiting-action":
        choices = session.get("allowed_actions", [])
        if session.get("protocol_evidence", {}).get("safe_to_cleanup") is not True or len(choices) != 1:
            raise Refusal("owned shutdown or the next task is unresolved; the installation and first attempts were retained")
        final = choices[0]["id"] == "finish-study"
        if pause_after_task and measured and not final:
            print("Paused between tasks. No engine is running. This configuration's files remain for resumption.")
            print("Run ./field-kit contribute again with the same source and Temper executable to continue.")
            return session
        session = workflow.submit_action(path, {**choices[0], "reason": "Continue only the selected configuration, preserving each first attempt."})
        measured = measured or not final
        write_checkpoint(session, repository, path)
    if session["state"] == "ready-to-finish":
        session = workflow.finish(path, lambda: True)
    if session["state"] == "complete" and session.get("cleanup") != "complete":
        session = workflow.run(path, lambda: True)
    return session


def contribute(arguments, repository, *, input_fn=input, facts_reader=read_machine_facts, runner=run_contributor_process):
    local = repository / ".local"
    if not local.is_dir() or local.is_symlink():
        raise Refusal("run ./setup.sh --install-only first to install private Python")
    if arguments.preview:
        return _run(arguments, repository, input_fn, facts_reader, runner)
    lock = local / "contributor.lock"
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(descriptor)
    with _exclusive_session_lock(lock):
        return _run(arguments, repository, input_fn, facts_reader, runner)


def _run(arguments, repository, input_fn, facts_reader, runner):
    local = (repository / ".local").resolve()
    source = QuestionCatalog.load(repository / "catalog/questions.json").find(SELECTOR)
    protocol = json.loads(source.files["protocol.json"])
    runs = retained_runs(local, source)
    unfinished = [row for row in runs if row[1]["state"] != "complete" or row[1].get("cleanup") != "complete"]
    if len(unfinished) > 1:
        raise Refusal("multiple unfinished configuration runs require recovery before another can start")
    requested = arguments.configuration
    if unfinished:
        path, session, identity = unfinished[0]
        if arguments.new or arguments.next or requested not in (None, identity):
            raise Refusal(f"resume and clean up the unfinished {identity} run before selecting another: {path}")
        if arguments.preview:
            print(f"Unfinished configuration {identity}: {path}. Run ./field-kit contribute to resume.")
            return 0
    else:
        # A historical unfinished run still owns its files and runtime identity.
        for name in ("contributor-session.json", "qwen-study-2-session.json", "qwen-study-3-session.json", "qwen-study-4-session.json"):
            pointer = local / name
            if pointer.exists() or pointer.is_symlink():
                if pointer.is_symlink():
                    raise Refusal(f"historical session pointer must not be a symlink: {pointer}")
                old = load_session(Path(json.loads(pointer.read_bytes())["session"]))
                if old["state"] != "complete" or old.get("cleanup") != "complete":
                    raise Refusal("keep the unfinished historical study in its producing checkout; start configuration runs in a separate checkout")
        previous = [row for row in runs if requested is None or row[2] == requested]
        if previous and not arguments.new and not arguments.next:
            path, session, _ = previous[-1]
            if not arguments.preview:
                write_results(path, repository / "runs" / path.name.removesuffix(".session.json"))
            print("This configuration run is complete; no task was repeated.")
            show_next(protocol, session["machine_facts"], runs)
            return 0
        path = session = identity = None
    temper = arguments.temper or local / "temper"
    facts, raw = facts_reader(temper)
    check_machine(facts.document)
    choices = available_configurations(protocol, facts.document)
    if path is None:
        if arguments.next:
            attempted = {row[2] for row in runs}
            pending = [item["id"] for item in choices if item["id"] not in attempted]
            if not pending:
                print("All applicable configurations have retained runs.")
                return 0
            identity = pending[0]
        else:
            identity = requested or (runs[-1][2] if arguments.new and runs else "splash-q4")
        if identity not in {item["id"] for item in choices}:
            raise Refusal("configuration is unavailable on this machine; choose " + ", ".join(item["id"] for item in choices))
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        root = local / ("qwen-5-" + timestamp + "-" + identity)
    else:
        root = Path(session["paths"]["root"])
    entry = select_package(source, identity)
    cells = selected_cells(protocol, identity)
    limit = min(facts.document["physical_memory_bytes"] * 3 // 4,
                facts.document["wired_limit_mib"] * 1024**2, 96 * 1024**3)
    if cells[0].get("candidate") and cells[0]["minimum_engine_memory_bytes"] > limit:
        raise Refusal("Q8 weights plus 4 GiB headroom exceed this machine's engine budget; no downloads or measurement started")
    cache = root / "hf-cache"
    if cache.is_symlink():
        raise Refusal("the private download cache must not be a symlink")
    # Absolute, session-owned cache survives task checkpoints and is removed by
    # the ordinary verified root cleanup. Never prune the user's shared cache.
    scoped_runner = partial(runner, environment={**os.environ, "HF_HUB_CACHE": str(cache)})
    workflow = Workflow(entry, facts, raw, temper, runner=scoped_runner)
    if path is None:
        plan = workflow.plan(root, "restore")
        configure_execution(temper, entry.package_root / cells[0]["lock"], cells[0]["preset"],
                            execution_settings(cells[0], limit), local / (root.name + "-preview.lock.json"),
                            dry_run=True, runner=scoped_runner)
        cost = entry.package["cost"]
        available = shutil.disk_usage(local).free
        print(f"\nQwen3.8 27B · {facts.document['chip']} · {identity}\n{source.availability_notice()}")
        print(entry.package["summary"])
        print(f"This configuration only: download ≤{cost['network_bytes_max']/1024**3:.1f} GiB; free disk allowance {cost['temporary_disk_bytes_max']/1024**3:.1f} GiB; retained evidence ≤1 GiB.")
        print(f"Time ceiling: {cost['live_runtime_minutes_max']/60:.1f} hours plus up to {cost['setup_minutes_max']/60:g} hours of initial setup. These are hard ceilings, not expected durations.")
        print("Coding requests allow up to 4 hours each; context requests up to 30 minutes. Each task has one retained first attempt.")
        print(f"Engine memory budget: {limit/1024**3:.1f} GiB. Stop at 512 MiB additional swap or thermal/observation limits.")
        print("New model downloads use a private cache. After verified shutdown, this configuration's installation and cache are removed. Existing shared caches are preserved.")
        print("Keep the Mac awake and on power. This run stops after the selected configuration. Results stay local.")
        print("Available configurations: " + ", ".join(item["id"] for item in choices))
        if arguments.preview:
            print(f"Available disk: {available/1024**3:.1f} GiB. Preview only; no session, downloads or inference.")
            return 0
        if available < cost["temporary_disk_bytes_max"]:
            raise Refusal(f"this configuration requires {cost['temporary_disk_bytes_max']/1024**3:.1f} GiB free; this volume has {available/1024**3:.1f} GiB")
        if not confirm("Run this configuration? [y/N]: ", input_fn=input_fn):
            return 0
        _, path = workflow.start(plan, plan.sha256)
    try:
        session = continue_run(workflow, path, repository, pause_after_task=arguments.pause_after_task)
    except (Exception, KeyboardInterrupt):
        print(f"Run retained at {path}. Resume with the same source and --temper executable; first attempts are never replayed.")
        raise
    if session["state"] == "complete" and session.get("cleanup") == "complete":
        write_results(path, repository / "runs" / path.name.removesuffix(".session.json"))
        print("Configuration complete. Its installation and private model cache have been removed.")
        show_next(protocol, facts.document, retained_runs(local, source))
    return 0
