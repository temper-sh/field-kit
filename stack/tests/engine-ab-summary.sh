#!/usr/bin/env bash
# engine-ab-summary.sh <label-a> <label-b> ... — tabulate engine-ab.sh output.
# Columns in the TSV: label, test, wall_ms, completion_tokens, prompt_tokens.
# Reads the same AB_OUT directory engine-ab.sh wrote to.
set -uo pipefail
SCRATCH="${AB_OUT:-${TMPDIR:-/tmp/}local-ai-ab}"

printf '%-18s %-14s %10s %8s %8s %12s\n' ARM TEST WALL_MS GEN_TOK PROMPT DERIVED
printf '%s\n' "------------------------------------------------------------------------------"
for L in "$@"; do
  F="$SCRATCH/ab-$L.tsv"
  [ -f "$F" ] || { echo "missing $F" >&2; continue; }
  grep -v '^#' "$F" | while IFS=$'\t' read -r arm test ms gen prompt; do
    [ "$ms" = "ERROR" ] && { printf '%-18s %-14s %10s\n' "$arm" "$test" ERROR; continue; }
    case "$test" in
      decode*)  d=$(awk -v g="$gen" -v m="$ms" 'BEGIN{printf "%.1f tok/s", g*1000/m}') ;;
      prefill*) d=$(awk -v p="$prompt" -v m="$ms" 'BEGIN{printf "%.0f tok/s", p*1000/m}') ;;
      *)        d=$(awk -v p="$prompt" -v m="$ms" 'BEGIN{printf "%.1fs @%d ctx", m/1000, p}') ;;
    esac
    printf '%-18s %-14s %10s %8s %8s %12s\n' "$arm" "$test" "$ms" "$gen" "$prompt" "$d"
  done
done

echo
echo "decode tok/s, mean of reps (wall-derived, identical method both arms):"
for L in "$@"; do
  grep -v '^#' "$SCRATCH/ab-$L.tsv" 2>/dev/null | awk -F'\t' -v l="$L" '
    $2 ~ /^decode/ && $3 != "ERROR" { s += $4*1000/$3; n++ }
    END { if (n) printf "  %-14s %.1f tok/s  (n=%d)\n", l, s/n, n }'
done
