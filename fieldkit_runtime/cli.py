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
    check = subparsers.add_parser("check", help="run the basic check of one preset, or continue an unfinished run",
                                  description="Install one catalog preset, run the Flask coding cases once and record"
                                              " speed and memory. Shows the plan and asks before downloading.")
    chosen = check.add_mutually_exclusive_group()
    chosen.add_argument("--preset", help="catalog preset ID; default: the first preset of the check that can run here")
    chosen.add_argument("--preset-file", type=Path, metavar="FILE",
                        help="run a Temper fork of a basic-check preset (ID.json) as a tuning trial")
    check.add_argument("--preview", action="store_true", help="check this Mac and show the plan; download no models")
    check.add_argument("--yes", action="store_true", help="accept the shown plan without asking")
    check.add_argument("--temper", type=Path, help="Temper executable; default: .local/temper")
    subparsers.add_parser("version", help="print the Field Kit runtime version")
    subparsers.add_parser("verify", help="check the runtime and the protocol's task files")
    return parser


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
