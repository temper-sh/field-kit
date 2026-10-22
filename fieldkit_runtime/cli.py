"""Agent-oriented tools. Commands execute work; the agent chooses the research."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
from . import __version__, context as runtime, grading, jobs, machine, storage, workloads
from .approval import preview, summary
from .common import Refusal, canonical_json
from .process import CommandFailure, atomic_write, capture_process
from .study import Study
from .temper import locate

REPOSITORY = Path(__file__).resolve().parents[1]

def parser():
    root = argparse.ArgumentParser(prog="field-kit", description="Research with an agent; measure with reusable tools.")
    commands = root.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="read installed Temper and machine facts; no installation")
    doctor.add_argument("--temper", type=Path)
    doctor.add_argument("--json", action="store_true")
    generate = commands.add_parser("workload", help="write a reusable request workload, without inference")
    generate.add_argument("kind", choices=("smoke", "tools", "engine", "context", "cache"))
    generate.add_argument("--records", type=int, default=256)
    generate.add_argument("--output-tokens", type=int, default=512)
    generate.add_argument("--out", type=Path, required=True)
    study = commands.add_parser("study", help="own material, run trials, wait, recover and return evidence")
    actions = study.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="review and approve exact candidates and limits")
    init.add_argument("id")
    init.add_argument("--brief", type=Path, required=True)
    init.add_argument("--lock", type=Path, action="append", default=[], help="exact Temper execution lock of a candidate preset")
    init.add_argument("--engine", type=Path, action="append", default=[],
                      help="local engine declaration (field-kit-local-engine/v1) for builds Temper cannot run yet")
    init.add_argument("--hours", type=float, default=8)
    init.add_argument("--download-gib", type=float, default=100)
    init.add_argument("--max-context", type=int, default=262144)
    init.add_argument("--request-seconds", type=int, default=1800)
    init.add_argument("--trial-seconds", type=int, default=14400)
    init.add_argument("--authoring-root", type=Path, help="Temper root holding the candidates' user presets, for Temper's check and plan")
    init.add_argument("--user-root", type=Path, help="Temper root of the user's layout; default ~/.temper")
    init.add_argument("--weights-cache", type=Path, help="Hugging Face hub cache, e.g. on an external SSD; default the user's")
    init.add_argument("--weights-cleanup", choices=("added", "keep"), default="added",
                      help="at close remove exact study-added weight files, or keep them")
    init.add_argument("--temper", type=Path)
    init.add_argument("--preview", action="store_true")
    init.add_argument("--yes", action="store_true", help="use existing owner approval for the displayed scope")
    run = actions.add_parser("run", help="run a workload within approved material; no quality gates")
    run.add_argument("id")
    run.add_argument("--candidate", required=True)
    run.add_argument("--workload", type=Path, required=True)
    run.add_argument("--model-adapter", help="format/replay adapter, e.g. flint/v1; overrides the workload")
    run.add_argument("--reason", required=True)
    run.add_argument("--lock", type=Path, help="Temper-compiled settings variant of this candidate")
    run.add_argument("--engine", type=Path, help="local engine variant: same binary and files, other arguments")
    run.add_argument("--temper", type=Path)
    run.add_argument("--background", action="store_true")
    for name, help_text in (("status", "show compact state and latest result"),
                            ("wait", "block until completion or timeout; no repeated agent polling"),
                            ("stop", "request owned worker shutdown"),
                            ("recover", "reconcile a dead worker without repeating inference"),
                            ("close", "remove owned runtime material after verified shutdown"),
                            ("export", "archive an idle study, including failures and repairs")):
        command = actions.add_parser(name, help=help_text)
        command.add_argument("id")
        if name == "wait":
            command.add_argument("--timeout", type=float, default=60)
        if name == "export":
            command.add_argument("--out", type=Path, required=True)
    repair = commands.add_parser("repair", help="attach a defect report and optional local patch for maintainers")
    repair.add_argument("id")
    repair.add_argument("--report", type=Path, required=True)
    repair.add_argument("--patch", type=Path)
    commands.add_parser("verify", help="verify bundled workload inputs without a model")
    commands.add_parser("grading", help="install the coding harness's Flask test packages into .local/grading, once")
    commands.add_parser("version")
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("id")
    worker.add_argument("run")
    return root

def doctor(path):
    temper = runtime.DEFAULT.temper(locate(path))
    identity = temper.identity()
    commands = {name: temper.supports(argv) for name, argv in (
        ("preset_fields", ["preset", "fields"]), ("preset_check", ["preset", "check"]),
        ("preset_plan", ["preset", "plan"]), ("preset_editor", ["preset", "edit"]),
        ("layout_status", ["layout", "status"]), ("layout_stop", ["layout", "stop"]),
        ("layout_activate", ["layout", "activate"]))}
    return {"temper": identity, "machine": temper.machine_facts(),
            "capabilities": commands, "python": sys.version.split()[0],
            "hf": shutil.which("hf"), "user_layout": machine.observe(temper, None),
            "weights_cache": storage.describe(storage.default_cache()), "instruction": machine.INSTRUCTION,
            "coding_grading": grading.describe(REPOSITORY),
            "effects": "read-only",
            "next": "Read docs/ASSISTANT.md; use installed capabilities before considering setup changes."}

def main(argv=None):
    arguments = parser().parse_args(argv if argv is not None else (sys.argv[1:] or ["--help"]))
    try:
        if arguments.command == "version":
            result = {"version": __version__}
        elif arguments.command == "doctor":
            result = doctor(arguments.temper)
        elif arguments.command == "verify":
            for kind in ("smoke", "tools", "engine", "context", "cache"):
                workloads.generate(kind)
            for path in (REPOSITORY / "workloads").glob("*.json"):
                workloads.load(path)
            harness = capture_process([sys.executable, "-B", str(REPOSITORY / "harnesses/coding/harness.py"), "verify"], 120)
            if harness.returncode:
                raise CommandFailure(["coding harness verify"], harness)
            result = {"verified": True, "version": __version__, "native_inference": False,
                      "coding_harness": json.loads(harness.stdout)["tasks"]}
        elif arguments.command == "grading":
            result = grading.prepare(REPOSITORY)
        elif arguments.command == "workload":
            if arguments.out.exists() or arguments.out.is_symlink():
                raise Refusal("workload output exists; choose a new file")
            atomic_write(arguments.out, canonical_json(workloads.generate(arguments.kind, arguments.records, arguments.output_tokens)))
            result = {"workload": str(arguments.out), "inference": False}
        else:
            study = Study(REPOSITORY, arguments.id, runtime.DEFAULT)
            if arguments.command == "repair":
                study.load()
                result = study.repair(arguments.report, arguments.patch)
            elif arguments.command == "_worker":
                from .attempt import execute
                result = execute(study, study.path_for(arguments.run))
            elif arguments.action == "init":
                temper = study.context.temper(locate(arguments.temper))
                document = preview(temper, arguments.lock, arguments.brief,
                    hours=arguments.hours, download_gib=arguments.download_gib,
                    max_context=arguments.max_context, request_seconds=arguments.request_seconds,
                    trial_seconds=arguments.trial_seconds, authoring_root=arguments.authoring_root,
                    user_root=arguments.user_root, weights_cache=arguments.weights_cache,
                    weights_cleanup=arguments.weights_cleanup, root=study.runtime(), context=study.context,
                    engines_local=arguments.engine)
                if arguments.preview:
                    result = summary(document)
                else:
                    print(json.dumps(summary(document), indent=2))
                    if not arguments.yes and input("Approve this scope? Type yes: ").strip().lower() != "yes":
                        return 0
                    state = study.initialize(document, temper)
                    layout = state["user_layout"]
                    result = {"study": str(study.directory), "state": "open",
                              "user_layout": ("stopped; restored at close" if layout.get("stopped") else
                                              "stop pending: " + layout["stop_error"] if layout.get("stop_error") else
                                              "nothing to stop"),
                              "restore": str(study.directory / "restore.md"),
                              "restore_command": state["restore_command"], "instruction": state["instruction"]}
            elif arguments.action == "run":
                directory = study.reserve(arguments.candidate, arguments.workload, arguments.reason,
                                          arguments.lock, arguments.temper, model_adapter=arguments.model_adapter,
                                          engine_path=arguments.engine)
                if arguments.background:
                    worker = jobs.start(REPOSITORY, arguments.id, directory.name)
                    result = {"run": directory.name, "pid": worker.pid, "state": "queued",
                              "wait": f"./field-kit study wait {arguments.id} --timeout 3600"}
                else:
                    from .attempt import execute
                    result = execute(study, directory)
            elif arguments.action == "recover":
                result = study.recover()
            elif arguments.action == "close":
                result = study.close()
            elif arguments.action == "export":
                from .export import export_study
                result = export_study(study, arguments.out)
            else:
                directory = study.latest()
                if directory is None:
                    result = {"state": study.load()["state"], "runs": 0}
                elif arguments.action == "wait":
                    if not 0 < arguments.timeout <= 86400:
                        raise Refusal("wait timeout must be positive and at most one day")
                    result = jobs.wait(directory, arguments.timeout)
                elif arguments.action == "stop":
                    result = jobs.stop(directory)
                else:
                    result = jobs.status(directory)
        print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and result.get("state") in ("failed", "retained", "needs-recovery") else 0
    except (Refusal, CommandFailure, OSError, ValueError, KeyError) as error:
        print(f"field-kit: {error}\nRecovery and return instructions: docs/REPAIR.md", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("field-kit: interrupted; inspect study status before retrying", file=sys.stderr)
        return 130
