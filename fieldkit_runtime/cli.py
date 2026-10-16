"""Field Kit command-line entry point."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .common import Refusal
from .process import CommandFailure, runtime_digest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="field-kit",
        description="Run local-AI experiments and collect results for review.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="print the Field Kit runtime version")
    subparsers.add_parser("verify", help="check that the runtime loads")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 9):
        print("field-kit: Python 3.9 or newer is required", file=sys.stderr)
        return 1
    parser = _parser()
    arguments = parser.parse_args(list(argv) if argv is not None else sys.argv[1:] or ["--help"])
    try:
        if arguments.command == "verify":
            print(f"FIELD-KIT verified runtime={runtime_digest()}")
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
