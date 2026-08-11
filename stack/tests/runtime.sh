#!/usr/bin/env bash

# shellcheck disable=SC2015
# The `cond && ok "..." || bad "..."` idiom is used throughout. It is safe here:
# ok() and bad() always return 0, so the `||` branch only fires when the
# condition itself failed.
#
# Live-stack checks against llama-swap on :8080. Covers acceptance items
# 7, 9, 10, 11 and the FINDINGS #8 streaming-tool-call regression.
#
# First run is slow: llama-server pulls the reranker (~0.6GB) and NuExtract3
# (~2.7GB + a 675MB mmproj) on first request. Later runs are quick.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${LOCAL_AI_BASE_URL:-http://localhost:8080}"
MANIFEST="$ROOT/models.yaml"

PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); return 0; }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); return 0; }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

curl -fsS -m 5 "$BASE/v1/models" >/dev/null 2>&1 || {
    printf '\033[31mllama-swap is not answering on %s\033[0m\n' "$BASE"
    printf '  launchctl start com.llamaswap.server\n'
    exit 1
}

running_ids() {
    curl -s -m 5 "$BASE/running" | jq -r '(.running // [])[].model' 2>/dev/null | tr '\n' ' '
}

# ── 7: served ids match the manifest exactly ─────────────────────────────────
hdr "check 7 — /v1/models matches the manifest"
want="$(yq -r '.models[] | select(.enabled != false) | .id' "$MANIFEST" | sort | tr '\n' ' ')"
got="$(curl -s -m 10 "$BASE/v1/models" | jq -r '.data[].id' | sort | tr '\n' ' ')"
printf '  manifest: %s\n  served:   %s\n' "$want" "$got"
[ "$want" = "$got" ] && ok "model ids match exactly" || bad "model id mismatch"

# ── 7b: the coder actually answers ───────────────────────────────────────────
hdr "check 7b — chat completion against the pinned coder"
resp="$(curl -s -m 900 "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d '{
  "model": "qwen3.6-27b-mlx",
  "messages": [{"role":"user","content":"Reply with exactly: ACCEPTANCE_OK"}],
  "max_tokens": 32, "temperature": 0}')"
content="$(printf '%s' "$resp" | jq -r '.choices[0].message.content // ""')"
printf '  content: %s\n' "${content:0:120}"
if [ -n "$content" ]; then
    ok "chat completion returned content"
else
    bad "no content — check: curl -s $BASE/logs/stream/upstream"
    printf '%s\n' "$resp" | head -c 400 | sed 's/^/        /'
fi

# ── 9: rerank returns real, ordered scores ───────────────────────────────────
# A GGUF missing cls.output.weight still returns HTTP 200 — it just scores
# everything at ~1e-20. Ordering AND magnitude both have to be checked.
hdr "check 9 — rerank scores are real and correctly ordered"
rr="$(curl -s -m 900 "$BASE/v1/rerank" -H 'Content-Type: application/json' -d '{
  "model": "rerank-qwen3-0.6b",
  "query": "How do I rotate the log files for a launchd service on macOS?",
  "documents": [
    "The recipe calls for two cups of flour, a pinch of salt, and butter at room temperature.",
    "Create /etc/newsyslog.d/llama-swap.conf with an owner:group field so newsyslog can rotate logs owned by your user account.",
    "Blue whales are the largest animals ever known to have lived on Earth."
  ]}')"
printf '%s' "$rr" | jq -c '.results // .' 2>/dev/null | head -c 400 | sed 's/^/  /'; echo
top="$(printf '%s' "$rr" | jq -r '(.results // []) | sort_by(-.relevance_score) | .[0].index // empty' 2>/dev/null)"
maxs="$(printf '%s' "$rr" | jq -r '[(.results // [])[].relevance_score] | max // 0' 2>/dev/null)"
[ "$top" = "1" ] && ok "the relevant document ranked first" \
                 || bad "top-ranked index was '$top', expected 1"
if awk -v m="$maxs" 'BEGIN{exit !(m > 0.001)}'; then
    ok "scores are non-degenerate (max=$maxs)"
else
    bad "DEGENERATE SCORES (max=$maxs) — GGUF is missing cls.output.weight; swap the repo"
fi

# ── 11: constrained decoding yields schema-valid JSON ────────────────────────
# Manifest-driven: a bucket manifest may route extraction to a non-local
# provider (PLAN §9, the 32GB candidate) — then there is no local extract
# model and this check skips rather than fails a healthy machine.
hdr "check 11 — extract_json path (response_format: json_schema)"
EXTRACT_ID="$(yq -r '.models[] | select(.kind == "extract" and .enabled != false) | .id' "$MANIFEST" | head -1)"
if [ -z "$EXTRACT_ID" ]; then
    printf '  \033[2mSKIP\033[0m  no local extract model — this manifest routes extraction elsewhere (remote group, or the coder json_schema fallback)\n'
else
ex="$(curl -s -m 900 "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d '{
  "model": "'"$EXTRACT_ID"'", "temperature": 0.2, "max_tokens": 512,
  "messages": [{"role":"user","content":[{"type":"text","text":"Invoice INV-4471 was issued by Contoso Ltd on 2026-03-14 for a total of 1290.50 EUR."}]}],
  "response_format": {"type":"json_schema","json_schema":{"name":"extraction","strict":true,"schema":{
     "type":"object",
     "properties":{"invoice_number":{"type":"string"},"vendor":{"type":"string"},
                   "issued":{"type":"string"},"total":{"type":"number"},
                   "currency":{"type":"string"}},
     "required":["invoice_number","vendor","total"]}}},
  "chat_template_kwargs": {"template":"{\n  \"invoice_number\": \"verbatim-string\",\n  \"vendor\": \"verbatim-string\",\n  \"issued\": \"date\",\n  \"total\": \"number\",\n  \"currency\": \"currency\"\n}","enable_thinking":false}}')"
raw="$(printf '%s' "$ex" | jq -r '.choices[0].message.content // ""')"
printf '  raw: %s\n' "${raw:0:200}"
if printf '%s' "$raw" | jq -e . >/dev/null 2>&1; then
    ok "output parses as JSON"
    printf '%s' "$raw" | jq -e 'has("invoice_number") and has("vendor") and has("total")' >/dev/null 2>&1 &&
        ok "all required properties present" || bad "required properties missing"
    printf '%s' "$raw" | jq -e '(.total|type) == "number"' >/dev/null 2>&1 &&
        ok "total is typed as a number" || bad "total is not a number"
    printf '%s' "$raw" | jq -e '.invoice_number == "INV-4471"' >/dev/null 2>&1 &&
        ok "verbatim field extracted correctly" || bad "invoice_number wrong"
else
    bad "not JSON — constrained decoding did not apply"
    printf '%s\n' "$ex" | head -c 400 | sed 's/^/        /'
fi
fi

# ── 10: residency after a heavy load matches the manifest's groups ───────────
# Until 2026-08-06 this asserted coder + reranker + extractor simultaneously
# resident. FINDINGS #16 retired that posture: specialists are now on-demand in
# a swap group (heavy members evict each other), so after check 11 loads the
# extractor, the reranker is *supposed* to be gone. The expectation is derived
# from the manifest so the check stays true on machines whose manifest keeps a
# specialist always-resident (group: utilities or ttl 0).
hdr "check 10 — residency after a heavy load matches the manifest's groups"
printf '  running: %s\n' "$(running_ids)"
case " $(running_ids) " in
    *" qwen3.6-27b-mlx "*) ok "qwen3.6-27b-mlx still resident (pinned)" ;;
    *) bad "qwen3.6-27b-mlx NOT running — a heavy load evicted the pinned coder" ;;
esac
# The most recently used heavy member is whichever heavy load ran last:
# check 11's extract model when the manifest has one, else check 9's
# reranker. Specialists are every enabled non-pinned entry — hardcoding ids
# here broke the first extractor-less candidate manifest.
LAST_HEAVY="${EXTRACT_ID:-}"
[ -z "$LAST_HEAVY" ] && LAST_HEAVY="$(yq -r '.models[] | select(.kind == "rerank" and .enabled != false) | .id' "$MANIFEST" | head -1)"
for m in $(yq -r '.models[] | select(.enabled != false and .group != "pinned") | .id' "$MANIFEST"); do
    grp="$(yq -r ".models[] | select(.id == \"$m\") | .group // \"\"" "$MANIFEST")"
    ttl="$(yq -r ".models[] | select(.id == \"$m\") | .ttl // 0" "$MANIFEST")"
    if [ "$grp" = "utilities" ] || [ "$ttl" = "0" ]; then
        expect="resident"       # manifest pins it beside the coder
    elif [ "$m" = "$LAST_HEAVY" ]; then
        expect="resident"       # most recently used heavy member
    else
        expect="swapped"        # heavy members swap among themselves
    fi
    case " $(running_ids) " in
        *" $m "*) present=1 ;;
        *)        present=0 ;;
    esac
    if [ "$expect" = "resident" ] && [ "$present" = "1" ]; then
        ok "$m resident, as the manifest intends ($grp, ttl $ttl)"
    elif [ "$expect" = "swapped" ] && [ "$present" = "0" ]; then
        ok "$m swapped out, as the manifest intends ($grp, ttl $ttl)"
    elif [ "$expect" = "resident" ]; then
        bad "$m NOT running — expected resident ($grp, ttl $ttl)"
    else
        bad "$m still resident — heavy members should swap (FINDINGS #16 posture not applied)"
    fi
done

# ── 12: streaming tool calls are parsed, not leaked as text ──────────────────
# Regression guard for FINDINGS #8. This MUST stream: without "stream": true a
# generic fallback parser covers for a missing tool parser and the check passes
# while every agent is broken. What we assert is a tool_calls delta; the failure
# mode is the raw <function=...> template text arriving as delta.content.
#
# tool_choice is pinned to the function because this check is about the parser,
# not about the model's willingness. Left to its own judgement on a bare prompt
# with no system prompt, the coder intermittently answers "I cannot access local
# files" instead of calling anything — a real but entirely different question,
# and one that would make this check flake.
hdr "check 12 — streaming tool calls are parsed (FINDINGS #8)"
sse="$(curl -s -N -m 900 "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d '{
  "model": "qwen3.6-27b-mlx", "stream": true, "max_tokens": 200, "temperature": 0,
  "tool_choice": {"type":"function","function":{"name":"read"}},
  "messages": [{"role":"user","content":"Read the file demo.txt and tell me what is on line 7."}],
  "tools": [{"type":"function","function":{"name":"read","description":"Read a file",
    "parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}}]}' \
  | grep '^data: ' | sed 's/^data: //' | grep -v '^\[DONE\]')"
tc="$(printf '%s\n' "$sse" | jq -r 'select(.choices[0].delta.tool_calls) | .choices[0].delta.tool_calls[0].function.name // empty' 2>/dev/null | head -1)"
leaked="$(printf '%s\n' "$sse" | jq -r '.choices[0].delta.content // empty' 2>/dev/null | tr -d '\n' | head -c 120)"
if [ -n "$tc" ]; then
    ok "streamed a parsed tool call (function: $tc)"
else
    bad "no tool_calls delta — the parser is not bound on the streaming path"
    printf '        leaked as content: %s\n' "${leaked:0:120}"
    printf '        fix: --tool-call-parser needs --enable-auto-tool-choice beside it\n'
fi

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
