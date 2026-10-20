"""Field Kit command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .common import Refusal
from .process import CommandFailure, runtime_digest

REPOSITORY = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="field-kit",
        description="Run local-AI experiments and collect results for review.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="measure one Qwen candidate (default: cheap smoke)",
                                  description="Run one bounded measurement. The study agent selects candidates and reviews quality.")
    chosen = check.add_mutually_exclusive_group()
    chosen.add_argument("--preset", help="catalog preset ID; default: the first preset of the check that can run here")
    chosen.add_argument("--preset-file", type=Path, metavar="FILE",
                        help="Temper fork or self-contained Qwen copy (ID.json), including alternate engines")
    _workload_options(check)
    check.add_argument("--python", type=Path, help="ready grading Python; required for coding quality")
    _quality_limits(check)
    check.add_argument("--case", help="one frozen coding case")
    look = check.add_mutually_exclusive_group()
    look.add_argument("--preview", action="store_true", help="check this Mac and list the presets; download no models")
    look.add_argument("--plan", action="store_true", help="print the exact plan a run asks consent for; create no run")
    look.add_argument("--background", action="store_true",
                      help="with --yes: run detached, keep the Mac awake, log to runs/background.log")
    check.add_argument("--yes", action="store_true", help="accept the plan without asking (only after reading it)")
    check.add_argument("--temper", type=Path, help="Temper executable; default: temper on PATH")
    study = subparsers.add_parser("study", help="plan, run and close an agent-directed Qwen study")
    actions = study.add_subparsers(dest="action", required=True)
    plan = actions.add_parser("plan", help="compile exact candidates and a bounded approval; download no models")
    plan.add_argument("--preset", action="append", default=[])
    plan.add_argument("--preset-file", type=Path, action="append", default=[])
    plan.add_argument("--python", type=Path, required=True, help="ready grading Python (pytest, asgiref, Flask dependencies)")
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--max-runs", type=int, default=12)
    plan.add_argument("--hours", type=float, default=8)
    plan.add_argument("--download-gib", type=float, default=100)
    plan.add_argument("--max-context", type=int, default=131072,
                      help="approved exploration ceiling; choose for the model/backend and work, not as a measured capacity")
    plan.add_argument("--request-seconds", type=int, default=1800,
                      help="request deadline; allow for long prefill, source replay and generation within the study budget")
    _quality_limits(plan)
    start = actions.add_parser("start", help="approve a saved study plan; no inference yet")
    start.add_argument("id")
    start.add_argument("--plan", type=Path, required=True)
    start.add_argument("--yes", action="store_true", help="accept a plan already approved by the machine owner")
    run = actions.add_parser("run", help="execute one question within an approved study")
    run.add_argument("id")
    run.add_argument("--candidate", required=True)
    run.add_argument("--preset-file", type=Path, help="a settings variant of this approved candidate")
    run.add_argument("--purpose", choices=("screen", "tune", "diagnose", "confirm"), default="screen")
    run.add_argument("--reason", required=True, help="evidence gap or previous result motivating this trial")
    run.add_argument("--background", action="store_true")
    _workload_options(run)
    others = []
    for action, help_text in (("status", "show decisions and evidence"), ("resume", "continue an unfinished study trial"),
                             ("close", "remove study-owned material after verified shutdown")):
        entry = actions.add_parser(action, help=help_text)
        entry.add_argument("id")
        others.append(entry)
    for action in (plan, start, run, *others):
        action.add_argument("--temper", type=Path)
    review = subparsers.add_parser("review", help="run sandboxed coding tests or attach a source review")
    review.add_argument("run", type=Path)
    review.add_argument("--python", type=Path, default=Path(sys.executable))
    review.add_argument("--assessment", type=Path, help="bind a reviewer-authored assessment to completed tests")
    subparsers.add_parser("status", help="show the background run and the newest run")
    subparsers.add_parser("stop", help="stop the background run the way Ctrl-C does")
    subparsers.add_parser("version", help="print the Field Kit runtime version")
    subparsers.add_parser("verify", help="check the runtime and the protocol's task files")
    return parser


def _workload_options(parser):
    parser.add_argument("--workload", choices=("smoke", "coding", "context", "cache"), default="smoke")
    parser.add_argument("--records", type=int, default=256, help="context/cache input size in records, not tokens")
    parser.add_argument("--output-tokens", type=int, default=512, help="synthetic request output allowance")
    parser.add_argument("--coding-mode", choices=("performance", "quality"), default="performance",
                        help="performance: one response; quality: bounded visible-test and repair loop")


def _quality_limits(parser):
    parser.add_argument("--quality-turns", type=int, default=8, help="maximum model responses per quality case, including final submission")
    parser.add_argument("--quality-seconds", type=int, default=14400, help="total time per quality case, including visible tests and repairs")


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 9):
        print("field-kit: Python 3.9 or newer is required", file=sys.stderr)
        return 1
    parser = _parser()
    arguments = parser.parse_args(list(argv) if argv is not None else sys.argv[1:] or ["--help"])
    try:
        if arguments.command == "check":
            from .check import command
            return command(arguments, REPOSITORY)
        if arguments.command == "study":
            from .study import command
            return command(arguments, REPOSITORY)
        if arguments.command == "review":
            from .review import command
            return command(arguments, REPOSITORY)
        if arguments.command in ("status", "stop"):
            from . import background
            if arguments.command == "stop":
                return background.stop(REPOSITORY)
            from .check import load_protocol
            return background.status(REPOSITORY, load_protocol(REPOSITORY)[0]["task"]["cases"])
        if arguments.command == "verify":
            from .check import load_protocol
            protocol, cases, protocol_sha256 = load_protocol(REPOSITORY)
            print(f"FIELD-KIT verified runtime={runtime_digest()} protocol=basic-check@{protocol['revision']}"
                  f" cases={len(cases)} sha256={protocol_sha256}")
            return 0
        if arguments.command == "version":
            print(f"field-kit {__version__}")
            return 0
    except (CommandFailure, Refusal, OSError, ValueError) as error:
        print(f"field-kit: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("field-kit: cancelled", file=sys.stderr)
        return 130
    parser.error("unsupported command")
    return 2
