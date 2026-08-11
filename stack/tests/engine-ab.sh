#!/usr/bin/env bash
# engine-ab.sh <port> <label> — the engine A/B that produced FINDINGS #13.
#
# Identical client path for both arms: same prompts, same wall-clock method,
# same derived metrics. Engine-reported counters are NOT used for the headline
# numbers, because the two engines report different things and mixing them is
# how FINDINGS #12's preliminary n=1 comparison ended up untrustworthy.
#
# Run each arm DIRECTLY on a scratch port with llama-swap stopped: 16GB of
# weights per engine means they cannot coexist on a 32GB box, and it keeps the
# live stack out of the measurement (same reasoning as tests/cache-ab.sh).
set -uo pipefail
PORT="${1:?usage: engine-ab.sh <port> <label> [reps]}"
LABEL="${2:?usage: engine-ab.sh <port> <label> [reps]}"
REPS="${3:-3}"
SCRATCH="${AB_OUT:-${TMPDIR:-/tmp/}local-ai-ab}"
mkdir -p "$SCRATCH" || { echo "cannot create $SCRATCH" >&2; exit 1; }
OUT="$SCRATCH/ab-$LABEL.tsv"
: > "$OUT"

# BSD date only gained %N recently; on older macOS it yields a literal "N" and
# every timing becomes garbage. Fail loudly here rather than silently.
now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }
now_ms >/dev/null 2>&1 || { echo "need python3 for millisecond timing" >&2; exit 1; }

# rapid-mlx rejects an unknown model id; llama-server ignores the field. Ask the
# server what it serves so both arms send a request the engine will accept.
# rapid-mlx answers OpenAI-shaped {data:[{id}]}; llama-server answers {models:[{name}]}.
# Through llama-swap /v1/models lists every manifest entry and .data[0] is
# whichever sorts first, so a multi-model proxy needs the id named explicitly:
# LOCAL_AI_AB_MODEL (same override tests/cache-ab.sh takes).
MODEL="${LOCAL_AI_AB_MODEL:-$(curl -s -m 10 "localhost:$PORT/v1/models" \
  | jq -r '(.data[0].id // .models[0].name // .models[0].model // empty)')}"
[ -n "$MODEL" ] && [ "$MODEL" != "null" ] || { echo "no model on :$PORT" >&2; exit 1; }
echo "# $LABEL model=$MODEL" | tee -a "$OUT"

api() { # $1 = json body -> full response body on stdout
  curl -s -m 900 "localhost:$PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' -d "$1"
}

err_row() { # $1 = test label, $2 = what came back
  printf '%s\t%s\tERROR\t%s\n' "$LABEL" "$1" \
    "$(printf '%s' "$2" | head -c 160 | tr '\n\t' '  ')" | tee -a "$OUT"
}

LAST_CONTENT=""
timed() { # $1 = label, $2 = json body; sets LAST_CONTENT
  local s e ms resp ct pt
  s=$(now_ms)
  resp=$(api "$2")
  e=$(now_ms)
  ms=$(( e - s ))
  LAST_CONTENT="?"

  # An engine that dies mid-run returns nothing, and a proxy error returns HTML.
  # Both must land as ERROR: this harness exists to compare wall clock, so a
  # dead arm silently scoring 0.0 tok/s is the one failure it cannot afford.
  # An OOM-killed engine under memory pressure is exactly that case.
  if [ -z "$resp" ]; then
    err_row "$1" "(empty response — engine died or curl timed out)"; return 0
  fi
  if ! printf '%s' "$resp" | jq -e . >/dev/null 2>&1; then
    err_row "$1" "(non-JSON response) $resp"; return 0
  fi

  ct=$(printf '%s' "$resp" | jq -r '.usage.completion_tokens // 0')
  pt=$(printf '%s' "$resp" | jq -r '.usage.prompt_tokens // 0')
  LAST_CONTENT=$(printf '%s' "$resp" | jq -r '.choices[0].message.content // "?"')
  if [ "${ct:-0}" = "0" ] && [ "${pt:-0}" = "0" ]; then
    err_row "$1" "$resp"; return 0
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$LABEL" "$1" "$ms" "$ct" "$pt" | tee -a "$OUT"
}

# ---------- fixtures ----------
# Deterministic across arms (identical bytes both sides), unique per invocation
# where a COLD cache is required — $$ and the epoch second vary the prefix.
STAMP="run$$-$(date +%s)"

BIG=""
i=0
while [ $i -lt 420 ]; do
  BIG="$BIG Section $i of the field manual describes the calibration procedure for barometric sensors installed at coastal observation stations, including the tolerances that apply before dawn readings commence."
  i=$((i+1))
done

DECODE_Q='Write a Python function that merges two sorted lists into one sorted list. Then write a second function that removes duplicates from a sorted list. Code only, no explanation.'

# ---------- 1. decode throughput ----------
# Short prompt, long generation: wall is dominated by decode, so wall-derived
# tok/s is a fair cross-engine number even though the engines report differently.
r=1
while [ $r -le "$REPS" ]; do
  timed "decode-r$r" "$(jq -n --arg m "$MODEL" --arg q "$DECODE_Q" \
    '{model:$m,messages:[{role:"user",content:$q}],max_tokens:300,temperature:0}')"
  r=$((r+1))
done

# ---------- 2. prefill throughput, cold ----------
# Long prompt, 8-token generation: wall is dominated by prefill. The $STAMP
# prefix guarantees this exact prompt has never been seen by either engine.
timed "prefill-cold" "$(jq -n --arg m "$MODEL" --arg q "$STAMP $BIG Reply with only the word ACK." \
  '{model:$m,messages:[{role:"user",content:$q}],max_tokens:8,temperature:0}')"

# ---------- 3. multi-turn reuse ----------
# The agent-shaped case: conversation grows by one exchange per turn.
MSGS="$SCRATCH/ab-$LABEL-msgs.json"
SYS=""
i=0
while [ $i -lt 100 ]; do
  SYS="$SYS The quarterly maintenance schedule for the coastal observation stations requires each operator to verify calibration of the barometric sensors before dawn readings commence."
  i=$((i+1))
done
jq -n --arg s "$SYS" '[{role:"system",content:$s}]' > "$MSGS"

FILLER='Please consider the following background before answering. The archive room holds ledgers from nineteen distinct field seasons, each catalogued by expedition number and cross-referenced against the specimen registry maintained in the annex. Retrieval requests must cite both identifiers.'
t=1
while [ $t -le 5 ]; do
  jq --arg q "Question $t: $FILLER Now, what is $t plus $t? Answer with the number only." \
     '. + [{role:"user",content:$q}]' "$MSGS" > "$MSGS.tmp" && mv "$MSGS.tmp" "$MSGS"
  BODY=$(jq -c --arg m "$MODEL" '{model:$m,messages:.,max_tokens:32,temperature:0}' "$MSGS")
  timed "turn-$t" "$BODY"
  jq --arg a "$LAST_CONTENT" '. + [{role:"assistant",content:$a}]' "$MSGS" > "$MSGS.tmp" && mv "$MSGS.tmp" "$MSGS"
  t=$((t+1))
done

# ---------- 4. byte-identical repeat (FINDINGS #10 / #12) ----------
timed "repeat-turn5" "$(jq -c --arg m "$MODEL" '{model:$m,messages:.[0:10],max_tokens:32,temperature:0}' "$MSGS")"

echo "--- $LABEL written to $OUT ---"
