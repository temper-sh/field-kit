"""Run or resume the frozen machine bucket after one concrete plan review."""
import datetime
import json
import os
from pathlib import Path
import re
import shutil

from ...catalog import QuestionCatalog, Refusal, canonical_json
from ...execution import configure_execution
from ...workflow import (Workflow, _atomic_write, _exclusive_session_lock, build_export,
                         load_session, run_contributor_process)
from .contributor import confirm, read_machine_facts
from .splash_study import SELECTOR, bucket, execution_settings, matrix


def check_machine(facts):
    selected = bucket(facts)
    chip = re.match(r"Apple M([0-9]+)(?:\b| )", facts["chip"])
    version = tuple(int(part) for part in facts["target"].get("distribution_version", "0").split(".")[:2])
    if not chip or int(chip[1]) < 3 or version < (26, 4):
        raise Refusal("Splash requires Apple M3 or newer and macOS 26.4 or newer")
    return selected


def format_report(session):
    facts, answers = session["machine_facts"], session.get("answers", {})
    rows = answers.get("completed-work", {}).get("value", {}).get("rows", [])
    def number(value):
        return "unmeasured" if value is None else f"{value:.2f}"
    lines = ["# Qwen engine and quant study", "", f"{facts['chip']} · {facts['physical_memory_bytes']/1024**3:g} GiB · macOS {facts['target']['distribution_version']}",
        f"Run: {session['id']} · {session['started_at']}", "",
        "| Configuration | Task | Outcome | Startup s | Request s | First output s | First answer s | Prefill t/s | Decode t/s | Observed output t/s |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        if not row["cases"]:
            lines.append(f"| {row['id']} | — | {row['status']} | — | — | — | — | — | — | — |")
        for case in row["cases"]:
            outcome = case.get("status", "passed" if case.get("correct") else "unsuccessful")
            lines.append("| " + " | ".join([row["id"], case["id"], outcome] + [number(case.get(key)) for key in
                ("startup_seconds", "service_seconds", "first_token_seconds", "first_answer_seconds", "prefill_tokens_per_second",
                 "generation_tokens_per_second", "observed_output_tokens_per_second")]) + " |")
    lines += ["", "| Configuration | Preparation s | Peak engine RSS GiB | Swap growth GiB | Failure |", "|---|---:|---:|---:|---|"]
    for row in rows:
        resources = row["resources"]
        rss = max((r.get("roles", {}).get("engine", {}).get("rss_bytes_max", 0) for r in resources), default=0)
        swap = max((r.get("swap_growth_bytes", 0) for r in resources), default=None)
        failure = (row.get("failure") or {}).get("message", "").replace("\n", " ").replace("|", "/")
        lines.append(f"| {row['id']} | {number((row.get('material') or {}).get('preparation_seconds'))} | {number(rss/1024**3 if rss else None)} | {number(swap/1024**3 if swap is not None else None)} | {failure} |")
    lines += ["", "Memory observations for preset review. Limits are configured allowances; process peaks are measured separately and are not added together.",
              f"Effective Metal budget: {facts['wired_limit_mib']/1024:g} GiB.",
              "", "| Configuration | Role | Peak RSS GiB | Peak footprint GiB |", "|---|---|---:|---:|"]
    for row in rows:
        roles = sorted({role for sample in row["resources"] for role in sample.get("roles", {})})
        for role in roles:
            peaks = []
            for metric in ("rss_bytes_max", "peak_footprint_bytes_max"):
                values = [sample["roles"][role][metric] for sample in row["resources"]
                          if metric in sample.get("roles", {}).get(role, {}) and sample["roles"][role][metric] > 0]
                peaks.append(number(max(values)/1024**3 if values else None))
            lines.append("| " + " | ".join([row["id"], role, *peaks]) + " |")
    context = answers.get("context", {}).get("value", {})
    lines += ["", f"Largest successful filled context: {context.get('highest_successful_window_tokens') or 'unmeasured'} tokens.",
        f"First unsuccessful point: {context.get('first_unsuccessful_window_tokens') or 'not observed'}.",
        "This is a bounded bracket with a correct continuation, not a model-wide maximum.", "",
        "Passing tests requires source review. The frozen async task has a known cleanup-error review caveat. Failed and partial first attempts remain in the JSON.",
        "Splash/llama use GGUF and Frog; Rapid/vLLM use shared MLX weights and their native template. These are composition results, not isolated engine effects.",
        "Native prefill/decode rates are unmeasured when the engine does not report their timing counters. Observed output rate includes streaming overhead and speculative batches.",
        "Storage-cold startup is unproven. Preparation, model startup and request time are separate. No measurements are extrapolated to another Mac.",
        "The JSON retains each preset's exact context identity, context/output settings and memory limit for review against Temper's preset cards. Only reviewed successful context points can support a tested-context choice; failures and untested points stay distinct.",
        "Results are local. Review generated patches, test results, machine facts and local paths before sharing.", ""]
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
    print(f"Report: {destination / 'report.md'}\nResult to review and send back: {destination / 'result.json'}")


def continue_study(workflow, session_path):
    session = workflow.resume(session_path)
    if session["state"] not in ("awaiting-action", "ready-to-finish", "complete"):
        session = workflow.run(session_path, lambda: True)
    while session["state"] == "awaiting-action":
        choices = session.get("allowed_actions", [])
        if session.get("protocol_evidence", {}).get("safe_to_cleanup") is not True or len(choices) != 1:
            raise Refusal("owned shutdown or the next matrix cell is unresolved; observations and installation were retained")
        session = workflow.submit_action(session_path, {**choices[0], "reason": "Continue the consented machine bucket, preserving every first attempt."})
    if session["state"] == "ready-to-finish":
        session = workflow.finish(session_path, lambda: True)
    if session["state"] == "complete" and session.get("cleanup") != "complete":
        session = workflow.run(session_path, lambda: True)
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
    local = repository / ".local"
    temper = arguments.temper or local / "temper"
    entry = QuestionCatalog.load(repository / "catalog/questions.json").find(SELECTOR)
    pointer = local / "qwen-study-4-session.json"
    if pointer.is_symlink():
        raise Refusal("contributor session pointer must not be a symlink")
    session_path = None
    if not arguments.new and not pointer.exists() and any((local / name).exists() for name in
            ("contributor-session.json", "qwen-study-2-session.json", "qwen-study-3-session.json")):
        raise Refusal("this clone contains an older study; use its producing checkout to resume or review it, or --new for a separate revision 4 study")
    if pointer.is_file() and not arguments.new:
        metadata = json.loads(pointer.read_bytes())
        session_path = Path(metadata["session"])
        if session_path.parent != local.resolve():
            raise Refusal("contributor session is outside this clone")
        session = load_session(session_path)
        if session["plan"]["sha256"] != metadata["plan_sha256"]:
            raise Refusal("contributor consent differs from session")
        if session["state"] == "complete" and session.get("cleanup") == "complete":
            if not arguments.preview:
                write_results(session_path, repository / "runs" / session_path.name.removesuffix(".session.json"))
            print("This run is complete. Use --new for a separate measurement.")
            return 0
        if arguments.preview:
            print(f"Unfinished study: {session_path}. Resume with the same --temper executable.")
            return 0
    facts, raw = facts_reader(temper)
    applicable, reasons = entry.applicable(facts)
    if not applicable:
        raise Refusal("study cannot run here: " + "; ".join(reasons))
    selected = check_machine(facts.document)
    workflow = Workflow(entry, facts, raw, temper, runner=runner)
    if session_path is None:
        protocol = json.loads(entry.files["protocol.json"])
        cells = matrix(protocol, facts.document)
        identity = "qwen-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        plan = workflow.plan(local.resolve() / identity, "restore")
        # Refuse an older development host before consent or materialization.
        first = cells[0]
        limit = min(facts.document["physical_memory_bytes"] * 3 // 4,
                    facts.document["wired_limit_mib"] * 1024**2, 96 * 1024**3)
        configure_execution(temper, entry.package_root / first["lock"], first["preset"],
                            execution_settings(first, limit), local.resolve() / (identity + "-preview.lock.json"),
                            dry_run=True, runner=runner)
        print(f"\nQwen3.8 27B · {facts.document['chip']} · bucket {selected}\n{entry.availability_notice()}")
        print("\n".join("  " + cell["id"] + (" (candidate after memory preflight)" if cell.get("candidate") else "") for cell in cells))
        cost = protocol["bucket_costs"][selected]
        required = cost["temporary_disk_bytes_max"]
        available = shutil.disk_usage(local).free
        print(f"\nThis bucket: download ≤{cost['network_bytes_max']/1024**3:.1f} GiB; free disk {required/1024**3:.1f} GiB; retained evidence ≤3 GiB.")
        definitions = {item["id"]: item for item in entry.package["investigation"]["actions"]}
        minutes = sum(definitions[cell["id"]]["runtime_minutes_max"] for cell in cells) + 90
        print(f"This route's time ceiling: {minutes/60:.1f} hours plus up to 6 hours of initial setup. These are hard ceilings, not expected durations.")
        print("Each coding request can take up to 4 hours; context requests up to 30 minutes. One first attempt per task and configuration.")
        print("Engine budget: min(75% physical RAM, effective Metal wired limit, 96 GiB). Stop at 512 MiB additional swap or thermal/observation limits.")
        print("Keep the Mac awake and on power. The dedicated installation is removed after verified shutdown. Results stay local; nothing is uploaded.")
        if arguments.preview:
            print(f"Available disk: {available/1024**3:.1f} GiB. Preview only; no study downloads or inference.")
            return 0
        if available < required:
            raise Refusal(f"the approved storage envelope requires {required/1024**3:g} GiB free; this volume has {available/1024**3:.1f} GiB")
        if not confirm("Start this study? [y/N]: ", input_fn=input_fn):
            return 0
        _, session_path = workflow.start(plan, plan.sha256)
        _atomic_write(pointer, canonical_json({"session": str(session_path), "plan_sha256": plan.sha256}))
    try:
        continue_study(workflow, session_path)
    except (Exception, KeyboardInterrupt):
        print(f"Study retained at {session_path}. Resume with the same source and --temper executable; completed attempts are never replayed.")
        raise
    write_results(session_path, repository / "runs" / session_path.name.removesuffix(".session.json"))
    return 0
