#!/usr/bin/env bash
#
# A/B a rapid-mlx serve flag against the agentic access pattern.
#
# This is the harness that established --hybrid-cache-entries 8 (docs/FINDINGS.md
# #1). It bypasses llama-swap and drives rapid-mlx directly on a scratch port, so
# it neither disturbs nor is disturbed by the live stack.
#
#   tests/cache-ab.sh --hybrid-cache-entries 8
#   tests/cache-ab.sh --pin-system-prompt
#   tests/cache-ab.sh --hybrid-cache-entries 8 --pin-system-prompt
#
# Everything after the script name is the "variant" — it is run with those extra
# flags and again without them, and the two are compared.
#
# The metric is rapid-mlx's own scheduler counter:
#
#     prompt_tokens=591 tokens_to_prefill=53
#                                        ^^ what it actually had to compute
#
# Wall-clock is reported too but is not the verdict: this box swaps under load
# and timings get noisy. tokens_to_prefill is structural and does not lie.
#
# Two things the first version of this test got wrong, both worth preserving:
#   - Sending a byte-identical request repeatedly shows ZERO reuse even when the
#     cache works, so the conversation has to grow.
#
#     The original explanation here — "the flag targets prefix extension" — was
#     wrong, and FINDINGS #10 corrected it: the cache *does* find the exact
#     repeat and reports `HIT cached=N remaining=0`, then the scheduler throws
#     the hit away. It has to re-forward the last prompt token for logits, which
#     means first trimming the saved cache by one, and this model is a hybrid
#     whose SSM layers use a non-trimmable ArraysCache. So it takes the
#     correctness-first fallback and full-prefills. Architectural, not a flag.
#   - The retained entry needs two turns to establish. Turn 2 reuses almost
#     nothing; the payoff starts at turn 3. Do not test fewer than 4 turns.

set -uo pipefail

MODEL="${LOCAL_AI_AB_MODEL:-froggeric/Qwen3.6-27B-MLX-4bit}"
PORT="${LOCAL_AI_AB_PORT:-10099}"
TURNS="${LOCAL_AI_AB_TURNS:-4}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cache-ab.XXXXXX")"

# Kill the server this script started, by pid. The old trap ran
# `pkill -f "port $PORT"`, which matches on a command line rather than on
# ownership — it would just as happily kill an editor session or another
# engine that merely mentioned the same port number.
SERVER_PID=""
KEEP_WORK=0
cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
        SERVER_PID=""
    fi
    [ "$KEEP_WORK" -eq 1 ] || rm -rf "$WORK"
}
trap cleanup EXIT

BREW_PREFIX="$(brew --prefix 2>/dev/null || echo /opt/homebrew)"
export PATH="$BREW_PREFIX/bin:$HOME/.local/bin:$PATH"
command -v rapid-mlx >/dev/null 2>&1 || { echo "rapid-mlx not on PATH" >&2; exit 1; }

[ $# -gt 0 ] || { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
VARIANT=("$@")

# Baseline flags: whatever the manifest uses, minus the flag under test.
#
# --enable-auto-tool-choice is not optional next to --tool-call-parser: alone,
# the parser flag silently disables tool calling entirely (FINDINGS #8). It
# makes no difference to prefill, so the old baseline measured fine — but this
# file gets read as an example of how to drive the engine, and an example that
# quietly breaks tool calling is worse than no example.
BASE_FLAGS=(
    --gpu-memory-utilization 0.85
    --no-thinking --enable-auto-tool-choice --tool-call-parser qwen3_coder
    --pflash off --kv-cache-dtype int8
)

SYS="You are a coding assistant. Answer in one word."
BODY="$(for i in $(seq 1 20); do
    echo "Module $i: the service layer validates the request, resolves the tenant, then dispatches to the domain handler."
done)"

turn() {
    local n="$1" msgs
    msgs="$(jq -n --arg s "$SYS" --arg b "$BODY" --argjson n "$n" '
      [{role:"system",content:$s},
       {role:"user",content:($b + "\n\nTurn 1: what layer validates?")}]
      + ([range(1;$n)] | map(
          [{role:"assistant",content:("Answer number " + (.+1|tostring) + " about the service layer architecture.")},
           {role:"user",content:("Turn " + (.+2|tostring) + ": ask again about module " + (.+1|tostring) + " please.")}]) | add // [])')"
    jq -n --arg m "$MODEL" --argjson msgs "$msgs" \
        '{model:$m, messages:$msgs, max_tokens:8, temperature:0}' |
    curl -s -m 900 -o /dev/null -w '%{time_total}' \
         "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' -d @-
}

run_variant() {
    local label="$1" log="$2"; shift 2
    printf '\n\033[1m%s\033[0m\n' "$label"

    rapid-mlx serve "$MODEL" "${BASE_FLAGS[@]}" "$@" --port "$PORT" \
        </dev/null >"$log" 2>&1 &
    SERVER_PID=$!
    local pid=$SERVER_PID waited=0
    while [ "$waited" -lt 500 ]; do
        curl -s -m 2 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1 && break
        kill -0 "$pid" 2>/dev/null || {
            printf '  \033[31mserver died\033[0m — last lines:\n'
            tail -5 "$log" | cut -c1-160 | sed 's/^/    /'
            return 1
        }
        sleep 4; waited=$((waited + 4))
    done
    printf '  booted in ~%ss\n' "$waited"

    local n t sched
    for n in $(seq 1 "$TURNS"); do
        t="$(turn "$n")"
        sched="$(LC_ALL=C grep -o 'prompt_tokens=[0-9]* tokens_to_prefill=[0-9]*' "$log" | tail -1)"
        printf '  turn %s: %7ss   %s\n' "$n" "$t" "$sched"
    done

    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; SERVER_PID=""; sleep 5
}

printf 'model:   %s\n' "$MODEL"
printf 'variant: %s\n' "${VARIANT[*]}"

run_variant "WITH ${VARIANT[*]}" "$WORK/variant.log" "${VARIANT[@]}"
run_variant "BASELINE (no ${VARIANT[*]})" "$WORK/baseline.log"

printf '\n\033[1mverdict\033[0m\n'
printf '  Compare the last turn of each. If tokens_to_prefill collapsed in the\n'
printf '  variant but tracks prompt_tokens in the baseline, the flag is working.\n'
printf '  Logs kept for this run only: %s\n' "$WORK"
KEEP_WORK=1   # cleanup() still stops the server; only the logs survive
