"""Contributor entry point and read-only maintenance commands."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .catalog import QuestionCatalog, Refusal, canonical_json
from .workflow import CommandFailure

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPOSITORY / "catalog" / "questions.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="field-kit",
        description="Run local-AI experiments and collect results for review.",
        epilog="This build includes the Qwen machine study. See docs/START.md to take part.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    contributor = subparsers.add_parser("contribute", help="run or resume the included experiment")
    contributor.add_argument("--temper", type=Path, help="matching Temper executable; required for this development revision")
    contributor.add_argument("--tuning", choices=("none", "both", "context", "flags"), help="Qwen study: choose optional tuning before reviewing the run")
    contributor.add_argument("--preview", action="store_true", help="read-only machine and cost preview")
    contributor.add_argument("--new", action="store_true", help="start a new allocation, retaining earlier results")
    subparsers.add_parser("version", help="print the Field Kit runtime version")
    witness = subparsers.add_parser("witness", help="verify and regrade a returned study result")
    witness.add_argument("--input", type=Path, required=True)
    witness.add_argument("--package", type=Path, required=True)
    verify = subparsers.add_parser("verify", help="verify question catalog, package, and protocol bytes")
    verify.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 9):
        print("field-kit: Python 3.9 or newer is required", file=sys.stderr)
        return 1
    parser = _parser()
    arguments_list = list(argv) if argv is not None else sys.argv[1:]
    if not arguments_list:
        if sys.stdin.isatty():
            arguments_list = ["contribute"]
        else:
            print("Field Kit runs local-AI experiments and records results for review.\nThis development build requires a matching Temper executable.\nUse ./field-kit contribute --temper /absolute/path/to/temper --preview to check the experiment.\nSee docs/START.md; keep dispatched studies in their original checkout.")
            return 0
    arguments = parser.parse_args(arguments_list)
    try:
        if arguments.command == "contribute":
            from .experiments.qwen.contributor import contribute
            return contribute(arguments, REPOSITORY)
        if arguments.command == "witness":
            from .experiments.qwen.witness import inspect as inspect_witness
            sys.stdout.buffer.write(canonical_json(inspect_witness(arguments.input, arguments.package)))
            return 0
        if arguments.command == "verify":
            catalog = QuestionCatalog.load(arguments.catalog)
            counts = {"active": 0, "qualifying": 0, "suspended": 0}
            for entry in catalog.questions:
                counts[entry.reference["availability"]] += 1
            print(
                f"FIELD-KIT verified revision={catalog.document['revision']} "
                f"sha256={hashlib.sha256(catalog.data).hexdigest()} "
                f"questions={len(catalog.questions)} active={counts['active']} "
                f"qualifying={counts['qualifying']} suspended={counts['suspended']}"
            )
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
