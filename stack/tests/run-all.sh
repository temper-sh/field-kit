#!/usr/bin/env bash
#
# Test entry point.
#
#   tests/run-all.sh              # offline only (default — fast, hermetic)
#   tests/run-all.sh --runtime    # + live-stack checks (needs llama-swap up)
#   tests/run-all.sh --perf       # + preload and prompt-cache (slow, restarts the service)
#   tests/run-all.sh --all

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RUN_OFFLINE=1; RUN_RUNTIME=0; RUN_PERF=0
while [ $# -gt 0 ]; do
    case "$1" in
        --offline) RUN_OFFLINE=1 ;;
        --runtime) RUN_RUNTIME=1 ;;
        --perf)    RUN_PERF=1 ;;
        --all)     RUN_RUNTIME=1; RUN_PERF=1 ;;
        -h|--help) sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         echo "unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

RC=0
[ "$RUN_OFFLINE" -eq 1 ] && { bash "$HERE/offline.sh" || RC=1; }
[ "$RUN_RUNTIME" -eq 1 ] && { bash "$HERE/runtime.sh" || RC=1; }
[ "$RUN_PERF"    -eq 1 ] && { bash "$HERE/perf.sh"    || RC=1; }

exit "$RC"
