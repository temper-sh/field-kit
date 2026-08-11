#!/usr/bin/env bash

# shellcheck disable=SC2015
# The `cond && ok "..." || bad "..."` idiom is used throughout. It is safe here:
# ok() and bad() always return 0, so the `||` branch only fires when the
# condition itself failed.
#
# Acceptance items 12 (preload) and 13 (prompt cache).
#
# Slow and disruptive: restarts the service and loads ~15GB. Not part of the
# default test run.
#
# Check 13 measures a *growing* conversation, not a repeated one. That
# distinction matters — see docs/FINDINGS.md #1. A byte-identical repeat shows
# no reuse even when the cache is working perfectly, and the retained entry
# needs two turns to establish itself.

set -uo pipefail

BASE="${LOCAL_AI_BASE_URL:-http://localhost:8080}"
CODER="${LOCAL_AI_CODER:-qwen3.6-27b-mlx}"

PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); return 0; }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); return 0; }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

running_ids() {
    curl -s -m 5 "$BASE/running" | jq -r '(.running // [])[].model' 2>/dev/null | tr '\n' ' '
}

# ── 12: the preload hook warms the coder with no client request ──────────────
# LOCAL_AI_PERF_NO_RESTART=1 skips this check entirely: it is hardwired to the
# launchd label, and a llama-swap that was started some other way (the field
# kit serves in the foreground, precisely to leave no launchd job behind) has
# its preload measured by whoever started it. Check 13 needs no restart and
# runs either way.
if [ "${LOCAL_AI_PERF_NO_RESTART:-0}" = "1" ]; then
    hdr "check 12 — preload after a service restart"
    printf '  skipped (LOCAL_AI_PERF_NO_RESTART=1 — no launchd service to restart)\n'
else
    hdr "check 12 — preload after a service restart"
    printf '  restarting com.llamaswap.server...\n'
    launchctl stop com.llamaswap.server 2>/dev/null || true
    sleep 3
    launchctl start com.llamaswap.server 2>/dev/null || true

    start=$(date +%s); found=0
    while [ $(( $(date +%s) - start )) -lt 240 ]; do
        case " $(running_ids) " in *" $CODER "*) found=1; break ;; esac
        sleep 3
    done
    elapsed=$(( $(date +%s) - start ))
    printf '  /running after %ss: %s\n' "$elapsed" "$(running_ids)"
    [ "$found" -eq 1 ] && ok "coder resident ${elapsed}s after restart, zero client requests" \
                       || bad "coder absent from /running after 240s"
fi

# ── 13: prompt cache across a growing conversation ───────────────────────────
hdr "check 13 — prefix cache across a growing conversation"

SYS="You are a coding assistant. Answer in one word."
BODY="$(for i in $(seq 1 20); do
    echo "Module $i: the service layer validates the request, resolves the tenant, then dispatches to the domain handler."
done)"

# Turn N replays turns 1..N-1 as history and appends a new question — exactly
# what an agent loop sends.
turn() {
    local n="$1" msgs
    msgs="$(jq -n --arg s "$SYS" --arg b "$BODY" --argjson n "$n" '
      [{role:"system",content:$s},
       {role:"user",content:($b + "\n\nTurn 1: what layer validates?")}]
      + ([range(1;$n)] | map(
          [{role:"assistant",content:("Answer number " + (.+1|tostring) + " about the service layer architecture.")},
           {role:"user",content:("Turn " + (.+2|tostring) + ": ask again about module " + (.+1|tostring) + " please.")}]) | add // [])')"
    jq -n --arg m "$CODER" --argjson msgs "$msgs" \
        '{model:$m, messages:$msgs, max_tokens:8, temperature:0}' |
    curl -s -m 900 -o /dev/null -w '%{time_total}' \
         "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d @-
}

declare -a TIMES
for n in 1 2 3 4 5; do
    t="$(turn "$n")"
    TIMES+=("$t")
    printf '  turn %s: %ss\n' "$n" "$t"
done

# rapid-mlx logs what it actually had to prefill. Far more reliable than wall
# clock, which is noisy under memory pressure.
printf '\n  scheduler counters (prompt_tokens vs tokens_to_prefill):\n'
sched="$(curl -s --max-time 6 "$BASE/logs/stream/upstream" 2>/dev/null |
         LC_ALL=C grep -o 'prompt_tokens=[0-9]* tokens_to_prefill=[0-9]*' | tail -5)"
if [ -n "$sched" ]; then
    printf '%s\n' "$sched" | sed 's/^/    /'
    last_sent="$(printf '%s' "$sched" | tail -1 | sed 's/.*prompt_tokens=\([0-9]*\).*/\1/')"
    last_pre="$(printf '%s'  "$sched" | tail -1 | sed 's/.*tokens_to_prefill=\([0-9]*\).*/\1/')"
    if [ -n "$last_pre" ] && [ "$last_pre" -lt $(( last_sent / 2 )) ]; then
        ok "final turn reused the prefix ($last_pre of $last_sent tokens prefilled)"
    else
        bad "no prefix reuse ($last_pre of $last_sent prefilled) — is --hybrid-cache-entries set?"
    fi
else
    printf '    \033[2m(upstream log unavailable — falling back to timings)\033[0m\n'
fi

# Latency must stop growing as history grows; that is the whole point.
t3="${TIMES[2]}"; t5="${TIMES[4]}"
if awk -v a="$t3" -v b="$t5" 'BEGIN{exit !(b <= a * 1.25)}'; then
    ok "latency flat as the conversation grows (turn 3 ${t3}s -> turn 5 ${t5}s)"
else
    bad "latency still climbing (turn 3 ${t3}s -> turn 5 ${t5}s)"
    printf '        \033[2mcheck --hybrid-cache-entries, prefix stability, ttl unloads,\n'
    printf '        and whether the box is swapping (sysctl vm.swapusage)\033[0m\n'
fi

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
