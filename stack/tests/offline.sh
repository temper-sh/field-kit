#!/usr/bin/env bash

# shellcheck disable=SC2015
# The `cond && ok "..." || bad "..."` idiom is used throughout. It is safe here:
# ok() and bad() always return 0, so the `||` branch only fires when the
# condition itself failed.
#
# Hermetic checks: no live service, no network, nothing outside a scratch dir.
# Covers acceptance items 1-6 and 8.
#
# Everything here runs against a throwaway HOME and HF_HOME, so it is safe to
# run on a configured machine without disturbing it.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/local-ai-tests.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); return 0; }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); return 0; }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# A copy of the project we can mutate (manifest edits, fake patches).
PROJ="$WORK/proj"
cp -R "$ROOT" "$PROJ"
# tests/ stays in the copy so check 1 lints the suite itself. It used to be
# deleted here, which meant the suite never linted its own scripts — that is
# how two unlinted ones landed in 2026-08.
rm -rf "$PROJ/.state"

# ── 1: syntax + shellcheck ───────────────────────────────────────────────────
hdr "check 1 — bash -n and shellcheck"
_syntax_ok=1
for f in "$PROJ/setup.sh" "$PROJ"/steps/*.sh "$PROJ"/scripts/*.sh "$PROJ"/tests/*.sh; do
    bash -n "$f" 2>/dev/null || { bad "bash -n: $f"; _syntax_ok=0; }
done
[ "$_syntax_ok" -eq 1 ] && ok "bash -n clean on all scripts"

if command -v shellcheck >/dev/null 2>&1; then
    # SC2329 is suppressed wholesale: shellcheck cannot follow the dynamic
    # source of steps/, so every helper in setup.sh looks uninvoked.
    if shellcheck -x -e SC2329 "$PROJ/setup.sh" "$PROJ"/steps/*.sh \
                                "$PROJ"/scripts/*.sh "$PROJ"/tests/*.sh \
                                >"$WORK/sc.txt" 2>&1; then
        ok "shellcheck clean"
    else
        bad "shellcheck findings:"; sed 's/^/        /' "$WORK/sc.txt" | head -30
    fi
else
    printf '  \033[2mSKIP\033[0m  shellcheck not installed\n'
fi

# ── 2: dry run changes nothing ───────────────────────────────────────────────
hdr "check 2 — --dry-run touches nothing"
H="$WORK/home-dry"; mkdir -p "$H"
if HOME="$H" HF_HOME="$WORK/hf-dry" "$PROJ/setup.sh" --dry-run >"$WORK/dry.txt" 2>&1; then
    # Only the paths setup.sh manages count. `brew list` populates
    # $HOME/Library/Caches/Homebrew all by itself, which is not our doing.
    _touched="$(find "$H/.config" "$H/.pi" "$H/Library/LaunchAgents" \
                     -type f 2>/dev/null)"
    if [ -z "$_touched" ]; then
        ok "dry run wrote nothing to ~/.config, ~/.pi or LaunchAgents"
    else
        bad "dry run created files:"; printf '%s\n' "$_touched" | sed 's/^/        /'
    fi
    grep -q 'dry run — nothing was changed' "$WORK/dry.txt" &&
        ok "dry run reported itself correctly" || bad "missing dry-run banner"
else
    bad "dry run exited non-zero"; tail -5 "$WORK/dry.txt" | sed 's/^/        /'
fi

# The same run on a machine that has never installed anything. yq arrives with
# the tools step, so a full dry-run legitimately has none — and this is the
# first command the README tells a cautious stranger to run, so it has to
# exit 0. It used to hard-fail. Homebrew's prefix is dropped from PATH to make
# yq genuinely absent rather than mocked.
FB="$WORK/fresh-bin"; FH="$WORK/home-fresh"; mkdir -p "$FB" "$FH"
if command -v brew >/dev/null 2>&1; then
    ln -sf "$(command -v brew)" "$FB/brew"
    if env -i HOME="$FH" PATH="$FB:/usr/bin:/bin:/usr/sbin:/sbin" TERM=dumb \
           "$PROJ/setup.sh" --dry-run >"$WORK/fresh.txt" 2>&1; then
        ok "fresh-machine dry run (no yq) exits 0"
    else
        bad "fresh-machine dry run exited non-zero"
        grep -E '\[fail\]' "$WORK/fresh.txt" | head -3 | sed 's/^/        /'
    fi
    grep -q 'yq not installed yet' "$WORK/fresh.txt" &&
        ok "and says why the manifest steps were skipped" ||
        bad "no explanation for the skipped manifest steps"
    # Same exclusion as above: brew populates its own cache under HOME when it
    # is merely queried, which is not something this installer did.
    [ -z "$(find "$FH/.config" "$FH/.pi" "$FH/Library/LaunchAgents" \
                 -type f 2>/dev/null)" ] &&
        ok "fresh dry run wrote none of the files it manages" ||
        bad "fresh dry run created managed files"
else
    printf '  \033[2mSKIP\033[0m  brew not installed\n'
fi

# ── 4: enabled toggle round-trips through both generated files ───────────────
hdr "check 4 — enabled: false removes a model from both configs"
H="$WORK/home-toggle"; mkdir -p "$H"
gen() { HOME="$H" "$PROJ/setup.sh" --only configs >/dev/null 2>&1; }
cfg() { yq -r '.models | keys | join(",")' "$H/.config/llama-swap/config.yaml" 2>/dev/null; }
pim() { jq -r '[.providers.local.models[].id] | join(",")' "$H/.pi/agent/models.json" 2>/dev/null; }

gen
BASE_CFG="$(cfg)"
cp "$H/.config/llama-swap/config.yaml" "$WORK/config.baseline.yaml"
[ -n "$BASE_CFG" ] && ok "baseline generated ($BASE_CFG)" || bad "baseline generation produced nothing"

# Disable the chat coder and one heavy specialist.
python3 - "$PROJ/models.yaml" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
for mid in ("qwen3.6-27b-mlx", "qwen3.5-4b"):
    s = re.sub(rf"(- id: {re.escape(mid)}\b)", r"\1\n    enabled: false", s, count=1)
open(p, "w").write(s)
PY
gen
case ",$(cfg)," in *,qwen3.6-27b-mlx,*) bad "disabled model still in config.yaml" ;;
                   *) ok "disabled models gone from config.yaml ($(cfg))" ;; esac
[ -z "$(pim)" ] && ok "disabled chat model gone from models.json" || bad "models.json still lists $(pim)"
yq -e '.routing.router.settings.groups | has("pinned") | not' \
   "$H/.config/llama-swap/config.yaml" >/dev/null 2>&1 &&
   ok "now-empty 'pinned' group omitted entirely" || bad "empty group still emitted"
yq -e 'has("hooks") | not' "$H/.config/llama-swap/config.yaml" >/dev/null 2>&1 &&
   ok "hooks block omitted when nothing preloads" || bad "empty hooks block still emitted"

# Re-enable and confirm we land back exactly where we started.
python3 - "$PROJ/models.yaml" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
open(p, "w").write(re.sub(r"\n    enabled: false", "", s))
PY
gen
if diff -q "$WORK/config.baseline.yaml" "$H/.config/llama-swap/config.yaml" >/dev/null 2>&1; then
    ok "re-enabling reproduces the original config byte-for-byte"
else
    bad "round-trip did not restore the original config"
    diff "$WORK/config.baseline.yaml" "$H/.config/llama-swap/config.yaml" | head -10 | sed 's/^/        /'
fi

# ── 5: generated config properties ───────────────────────────────────────────
hdr "check 5 — generated config.yaml is correctly shaped"
C="$H/.config/llama-swap/config.yaml"
_missing="$(yq -r '.models | to_entries[] | select(.value.cmd|test("rapid-mlx")) | select(.value.useModelName == null) | .key' "$C")"
[ -z "$_missing" ] && ok "every rapid-mlx entry has useModelName" \
                   || bad "rapid-mlx entries missing useModelName: $_missing"
_extra="$(yq -r '.models | to_entries[] | select(.value.cmd|test("llama-server")) | select(.value.useModelName != null) | .key' "$C")"
[ -z "$_extra" ] && ok "no llama-server entry has useModelName" \
                 || bad "llama-server entries wrongly carry useModelName: $_extra"
yq -e '.models."rerank-qwen3-0.6b".cmd | test("--reranking")' "$C" >/dev/null 2>&1 &&
    ok "kind: rerank produced --reranking" || bad "rerank kind-flag missing"
_want="$(yq -r '.models[] | select(.id == "qwen3.6-27b-mlx") | .ttl' "$PROJ/models.yaml")"
yq -e ".models.\"qwen3.6-27b-mlx\".ttl == $_want" "$C" >/dev/null 2>&1 &&
    ok "manifest ttl ($_want) survived into the config" \
    || bad "manifest ttl ($_want) clobbered: config says $(yq -r '.models."qwen3.6-27b-mlx".ttl' "$C")"
# ttl: 0 is falsy and must not be replaced by the 1800 default. It was the
# coder's live value until 2026-08-06 (FINDINGS #17 gave it ttl 7200), so the
# zero case is pinned here by a fixture edit rather than by the manifest.
python3 - "$PROJ/models.yaml" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
open(p, "w").write(s.replace("ttl: 7200", "ttl: 0", 1))
PY
gen
yq -e '.models."qwen3.6-27b-mlx".ttl == 0' "$C" >/dev/null 2>&1 &&
    ok "ttl: 0 survives generation (not replaced by the default)" || bad "ttl: 0 was clobbered"
python3 - "$PROJ/models.yaml" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
open(p, "w").write(s.replace("ttl: 0", "ttl: 7200", 1))
PY
gen
yq -e '.models."qwen3.6-27b-mlx".cmd | test("--gpu-memory-utilization 0.85")' "$C" >/dev/null 2>&1 &&
    ok "gpu_memory_utilization default propagated" || bad "gmu missing from cmd"
grep -q '^# GENERATED by local-ai-setup' "$C" &&
    ok "config.yaml carries the GENERATED header" || bad "missing GENERATED header"
# Specialists belong in llama-swap but must be invisible to Pi. The specimen
# comes from the manifest (first enabled non-chat entry), not a hardcoded id —
# the 32GB posture retired the extractor and this check must not assert a
# residency the manifest no longer declares.
_spec="$(yq -r '.models[] | select(.enabled != false) | select((.kind // "chat") != "chat") | .id' "$PROJ/models.yaml" | head -1)"
if [ -n "$_spec" ]; then
    yq -e ".models | has(\"$_spec\")" "$C" >/dev/null 2>&1 &&
        ok "non-chat model ($_spec) present in config.yaml" || bad "specialist $_spec missing from config.yaml"
    case ",$(pim)," in *,"$_spec",*) bad "specialist leaked into Pi's picker" ;;
                       *) ok "non-chat models absent from models.json" ;; esac
else
    ok "manifest declares no non-chat specialist — nothing to assert"
fi

# ── 6: models.json merge preserves foreign providers ─────────────────────────
hdr "check 6 — models.json merge preserves everything else"
mkdir -p "$H/.pi/agent"
cat >"$H/.pi/agent/models.json" <<'JSON'
{
  "providers": {
    "openrouter": { "baseUrl": "https://openrouter.ai/api/v1", "api": "openai-completions",
                    "apiKey": "sk-fake", "models": [{"id": "some/model", "name": "Should Survive"}] },
    "local": { "baseUrl": "http://stale:1/v1", "api": "openai-completions",
               "apiKey": "x", "models": [{"id": "stale-model"}] }
  },
  "unrelatedTopLevel": { "keep": true }
}
JSON
gen
J="$H/.pi/agent/models.json"
jq -e '.providers.openrouter.models[0].name == "Should Survive"' "$J" >/dev/null 2>&1 &&
    ok "foreign provider survived" || bad "foreign provider was clobbered"
jq -e '.unrelatedTopLevel.keep == true' "$J" >/dev/null 2>&1 &&
    ok "unrelated top-level key survived" || bad "unrelated key was dropped"
jq -e '[.providers.local.models[].id] | index("stale-model") == null' "$J" >/dev/null 2>&1 &&
    ok "stale local provider was replaced" || bad "stale local entry persisted"

# ── 8: patch self-heal ───────────────────────────────────────────────────────
hdr "check 8 — patches are hash-based and self-healing"
PH="$WORK/home-patch"; HF="$WORK/hf-patch"
SNAP="$HF/hub/models--fakeorg--Fake-Qwen-MLX-4bit/snapshots/deadbeef"
mkdir -p "$PH" "$SNAP"
echo "not-real-weights" >"$SNAP/model-00001-of-00001.safetensors"
echo "STOCK TEMPLATE — the broken upstream one" >"$SNAP/chat_template.jinja"
cat >"$PROJ/models.yaml.patchtest" <<'YAML'
defaults:
  ttl: 1800
  gpu_memory_utilization: 0.85
models:
  - id: fake-patched-model
    engine: rapid-mlx
    repo: fakeorg/Fake-Qwen-MLX-4bit
    patches: [qwen-fixed-template]
YAML
cp "$PROJ/models.yaml" "$WORK/models.real.yaml"
cp "$PROJ/models.yaml.patchtest" "$PROJ/models.yaml"

# The template is fetched, not vendored (patches/qwen-fixed-template/SOURCE.md).
# $PROJ is a throwaway copy, so repoint its FETCH at a local file:// URL: the
# real fetch path runs end to end — download, cache, hash, apply — and the
# suite still never touches the network.
UPSTREAM="$WORK/fake-upstream-template.jinja"
echo "FIXED TEMPLATE — what upstream would have served" >"$UPSTREAM"
cat >"$PROJ/patches/qwen-fixed-template/FETCH" <<FETCH
chat_template.jinja file://$UPSTREAM
FETCH
WANT="$(shasum -a 256 "$UPSTREAM" | cut -d' ' -f1)"
# Redirect to a file rather than piping into grep -q: under `set -o pipefail`,
# grep -q exits on the first match, setup.sh takes SIGPIPE, and the pipeline
# reports failure even though everything worked.
run_models() { HOME="$PH" HF_HOME="$HF" "$PROJ/setup.sh" --only models >"$WORK/patch.txt" 2>&1; }
saw()        { grep -q "$1" "$WORK/patch.txt"; }
hash_now()   { shasum -a 256 "$SNAP/chat_template.jinja" | cut -d' ' -f1; }

run_models
saw '\[patch\]' && ok "stock template triggered [patch]" || bad "no [patch] on first pass"
[ "$(hash_now)" = "$WANT" ] && ok "target now matches the fetched hash" \
                            || bad "hash mismatch after patching"

run_models
saw '\[ok\].*chat_template' && ok "second pass is [ok] (idempotent)" \
                            || bad "re-patched an already-correct file"

echo "CORRUPTED" >"$SNAP/chat_template.jinja"
run_models
saw '\[patch\]' && ok "corruption re-triggered [patch] (self-heal)" \
                || bad "corruption not detected"
[ "$(hash_now)" = "$WANT" ] && ok "converged back to the fetched hash" \
                            || bad "did not converge after corruption"

# In a real HF cache the snapshot entry is a symlink into blobs/, shared
# between revisions. Patching must replace the link, never write through it.
BLOBS="$HF/hub/models--fakeorg--Fake-Qwen-MLX-4bit/blobs"
mkdir -p "$BLOBS"
echo "SHARED BLOB — must not be modified" >"$BLOBS/sharedtemplate"
BLOB_BEFORE="$(shasum -a 256 "$BLOBS/sharedtemplate" | cut -d' ' -f1)"
rm -f "$SNAP/chat_template.jinja"
ln -s ../../blobs/sharedtemplate "$SNAP/chat_template.jinja"

run_models
if [ "$(shasum -a 256 "$BLOBS/sharedtemplate" | cut -d' ' -f1)" = "$BLOB_BEFORE" ]; then
    ok "shared blob left untouched (link replaced, not written through)"
else
    bad "patch wrote through the symlink and corrupted the shared blob"
fi
[ -L "$SNAP/chat_template.jinja" ] && bad "snapshot entry is still a symlink" \
                                   || ok "snapshot entry is now a real file"
[ "$(hash_now)" = "$WANT" ] && ok "symlinked target patched to the fetched hash" \
                            || bad "symlink case did not converge"

cp "$WORK/models.real.yaml" "$PROJ/models.yaml"

# ── 14: the two sudo scripts are portable and re-runnable ────────────────────
# These exist to be correct on a machine that is not this one, so the parts that
# would silently bake in *this* laptop are what get asserted: the memory target
# and the log owner. Neither needs root — the identity and RAM probes are
# overridable, and the config is written to a scratch path.
hdr "check 14 — sudo scripts derive their values from the machine"

GPU_SH="$PROJ/scripts/gpu-wired-limit.sh"
ROT_SH="$PROJ/scripts/log-rotation.sh"

# 32GB must still give 24576: every measurement in FINDINGS was taken there, and
# a formula that quietly moved it would invalidate them.
_t32="$(LOCALAI_MEMSIZE_BYTES=$((32 * 1073741824)) "$GPU_SH" --target)"
[ "$_t32" = "24576" ] && ok "32GB -> 24576 MB (the measured configuration)" \
                      || bad "32GB -> $_t32 MB, expected 24576"

# 75% of RAM, except the 8GB system reserve binds on smaller machines.
_t16="$(LOCALAI_MEMSIZE_BYTES=$((16 * 1073741824)) "$GPU_SH" --target)"
_t64="$(LOCALAI_MEMSIZE_BYTES=$((64 * 1073741824)) "$GPU_SH" --target)"
[ "$_t16" = "8192" ] && [ "$_t64" = "49152" ] \
    && ok "scales with RAM (16GB -> 8192, 64GB -> 49152)" \
    || bad "scaling wrong: 16GB -> $_t16 (want 8192), 64GB -> $_t64 (want 49152)"

LOCALAI_MEMSIZE_BYTES=$((8 * 1073741824)) "$GPU_SH" --target >/dev/null 2>&1 \
    && bad "8GB machine produced a target instead of refusing" \
    || ok "refuses a machine too small to hold the stack"

# The trap this replaces: under sudo, `id -un` is root, so a config generated
# the obvious way rotates root's logs and leaves llama-swap's unrotated.
_owner="$(SUDO_USER=alex LOCALAI_LOG_HOME=/Users/alex "$ROT_SH" --dry-run | grep -c '^/.*alex:')"
[ "$_owner" -eq 2 ] && ok "owner comes from SUDO_USER, not root" \
                    || bad "expected 2 alex-owned lines under sudo, got $_owner"

SUDO_USER=nosuchuser "$ROT_SH" --dry-run >/dev/null 2>&1 \
    && bad "accepted an unresolvable home instead of refusing" \
    || ok "refuses an account whose home cannot be resolved"

# Applying twice must converge, and --check must agree with what was written.
_conf="$WORK/newsyslog.conf"
LOCALAI_NEWSYSLOG_CONF="$_conf" "$ROT_SH" >/dev/null 2>&1
_second="$(LOCALAI_NEWSYSLOG_CONF="$_conf" "$ROT_SH" 2>&1)"
case "$_second" in
    *"nothing to do"*) ok "second apply is a no-op" ;;
    *)                 bad "second apply was not idempotent: $_second" ;;
esac
LOCALAI_NEWSYSLOG_CONF="$_conf" "$ROT_SH" --check >/dev/null 2>&1 \
    && ok "--check agrees with the written config" \
    || bad "--check disagreed with the config it had just written"
rm -f "$_conf"

# ── 15: bad manifests are refused, and never reach a live config ─────────────
# Every case here is legal YAML going in. What makes them dangerous is that
# they are wrong on the way *out*, silently: before these guards, setup printed
# [install] and exited 0 while llama-swap's --watch-config picked the broken
# file up on a service that had been working a second earlier.
hdr "check 15 — invalid manifests are rejected before anything is written"
VH="$WORK/home-valid"; mkdir -p "$VH"
VP="$WORK/proj-valid"; cp -R "$ROOT" "$VP"; rm -rf "$VP/.state"
vrun()  { HOME="$VH" "$VP/setup.sh" --only configs >"$WORK/valid.txt" 2>&1; }
vsaw()  { grep -q "$1" "$WORK/valid.txt"; }
vman()  { cat >"$VP/models.yaml"; }

# A known-good baseline first: the point is that a later bad manifest must not
# be able to replace it.
vman <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: good-model
    engine: rapid-mlx
    repo: org/Good
YAML
vrun
VLIVE="$VH/.config/llama-swap/config.yaml"
VSUM="$(shasum -a 256 "$VLIVE" | cut -d' ' -f1)"
[ -n "$VSUM" ] && ok "baseline config generated" || bad "baseline generation failed"

# yq follows YAML 1.2: `no` is the string "no", so `.enabled != false` is true
# and the "parked" model deploys.
vman <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: parked-model
    engine: rapid-mlx
    repo: org/Parked
    enabled: no
YAML
vrun
vsaw 'is not a boolean' && ok "enabled: no is rejected, not silently deployed" \
                       || bad "enabled: no was accepted"

# Interpolated into `name: "%s"` — an unescaped quote ends the scalar early.
vman <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: quoted-model
    display_name: 'The "Best" Coder'
    engine: rapid-mlx
    repo: org/Quoted
YAML
vrun
vsaw "quote, backslash or newline" && ok "a quote in display_name is rejected" \
                                  || bad "quoted display_name was accepted"

# Read by index rather than by id, because mval() would interpolate this id
# into its own yq query and quietly return nothing.
vman <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: 'we"ird'
    engine: rapid-mlx
    repo: org/Weird
YAML
vrun
vsaw "quote, backslash or newline" && ok "a quote in the id itself is caught" \
                                  || bad "quoted id slipped through"

# A block scalar is idiomatic YAML and breaks the generated `cmd: >` block.
vman <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: block-flags
    engine: rapid-mlx
    repo: org/Block
    flags: |-
      --no-thinking
      --tool-call-parser qwen3_coder
YAML
vrun
vsaw "spans multiple lines" && ok "multi-line flags are rejected" \
                           || bad "multi-line flags were accepted"

# The backstop: ttl is numeric, so no string rule applies, yet it renders an
# unclosed flow sequence. Only parsing the output catches this.
vman <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: good-model
    engine: rapid-mlx
    repo: org/Good
    ttl: "[unclosed"
YAML
vrun
vsaw 'not valid yaml' && ok "parse gate catches what field rules cannot" \
                      || bad "invalid YAML passed the parse gate"
[ -f "$VP/.state/invalid-config.yaml" ] && ok "the rejected render was kept for inspection" \
                                        || bad "no invalid-*.yaml left behind to debug with"
[ "$(shasum -a 256 "$VLIVE" | cut -d' ' -f1)" = "$VSUM" ] \
    && ok "the live config was never touched by any of the above" \
    || bad "a rejected manifest reached the live config"
if vrun; then bad "exited 0 despite refusing to install"
else          ok "a rejected manifest exits non-zero"; fi

# Quotes inside flags must still work — --chat-template-kwargs needs them, and
# flags are emitted bare inside `cmd: >` where quoting is not in play.
vman <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: good-model
    engine: llama-server
    repo: org/Good
    flags: "--chat-template-kwargs '{\"enable_thinking\":false}' -b 512"
YAML
vrun
yq -e '.models."good-model".cmd | test("enable_thinking")' "$VLIVE" >/dev/null 2>&1 \
    && ok "quotes inside flags still generate (not over-rejected)" \
    || bad "a legal flags value was rejected"

# ── 16: the generated plist derives its values from the machine ──────────────
# Same rule as the sudo scripts in check 14: nothing measured on this laptop may
# be baked into a file a stranger installs.
hdr "check 16 — the launchd plist is derived, not hardcoded"
grep -q '/opt/homebrew' "$PROJ/templates/com.llamaswap.server.plist.tmpl" &&
    bad "template still hardcodes /opt/homebrew" ||
    ok "template asks brew for its prefix instead of assuming one"

PH2="$WORK/home-plist"; mkdir -p "$PH2/Library/LaunchAgents"
plist_env() { /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:$1" \
                  "$PH2/Library/LaunchAgents/com.llamaswap.server.plist" 2>/dev/null; }

# A custom HF cache must reach the engines. They run under launchd with a bare
# environment, so if the plist does not carry it the weights are downloaded to
# one place and looked for in another.
LOCALAI_SKIP_LAUNCHCTL=1 HOME="$PH2" HF_HOME="$WORK/custom-hf" \
    "$PROJ/setup.sh" --only service >/dev/null 2>&1
[ "$(plist_env HF_HUB_CACHE)" = "$WORK/custom-hf/hub" ] &&
    ok "HF_HOME propagates into the service environment" ||
    bad "plist HF_HUB_CACHE is $(plist_env HF_HUB_CACHE), expected $WORK/custom-hf/hub"

# The KV checkpoint cap scales with free disk and never exceeds rapid-mlx's own
# 20 GiB default (FINDINGS #14).
cap_gib() {
    LOCALAI_SKIP_LAUNCHCTL=1 HOME="$PH2" LOCALAI_FREE_KB="$1" \
        "$PROJ/setup.sh" --only service >/dev/null 2>&1
    echo $(( $(plist_env RAPID_MLX_KV_CHECKPOINT_MAX_BYTES) / 1073741824 ))
}
_big="$(cap_gib $((1024 * 1048576)))"
_mid="$(cap_gib $((100 * 1048576)))"
_small="$(cap_gib $((10 * 1048576)))"
[ "$_big" = "20" ] && ok "1TB free caps at rapid-mlx's own 20 GiB default" \
                   || bad "1TB free produced ${_big} GiB, expected 20"
[ "$_mid" = "10" ] && ok "100GB free scales down to 10 GiB" \
                   || bad "100GB free produced ${_mid} GiB, expected 10"
[ "$_small" = "1" ] && ok "a nearly-full disk floors at 1 GiB" \
                    || bad "10GB free produced ${_small} GiB, expected 1"

# ── 17: the extension's deterministic parsers ────────────────────────────────
# These are pure functions and were never exercised in production — the two
# bugs this harness found on its first run had been shipping quietly.
hdr "check 17 — compact-test-output's parsers behave"
if command -v node >/dev/null 2>&1; then
    if node --test "$PROJ/tests/compact-test-output.test.mjs" >"$WORK/units.txt" 2>&1; then
        _n="$(grep -E '^# pass |^ℹ pass ' "$WORK/units.txt" | grep -oE '[0-9]+' | head -1)"
        ok "node --test: ${_n:-all} assertions pass"
    else
        bad "unit tests failed:"
        grep -E '✖|AssertionError|actual:|expected:' "$WORK/units.txt" | head -12 | sed 's/^/        /'
    fi
else
    printf '  \033[2mSKIP\033[0m  node not installed\n'
fi

# ── 18: project_search candidate/rerank/logging boundaries ───────────────────
hdr "check 18 — project_search behaves"
if command -v node >/dev/null 2>&1; then
    if node --test "$PROJ/tests/project-search.test.mjs" >"$WORK/search-units.txt" 2>&1; then
        _n="$(grep -E '^# pass |^ℹ pass ' "$WORK/search-units.txt" | grep -oE '[0-9]+' | head -1)"
        ok "node --test: ${_n:-all} project_search assertions pass"
    else
        bad "project_search unit tests failed:"
        grep -E '✖|AssertionError|actual:|expected:' "$WORK/search-units.txt" | head -12 | sed 's/^/        /'
    fi
else
    printf '  \033[2mSKIP\033[0m  node not installed\n'
fi

# ── 19: the GPU budget advisory derives from machine and manifest ────────────
# FINDINGS #16: a fixed utilization fraction leaves a margin proportional to
# RAM while the co-resident reserve is absolute. The assertions pin the shape:
# the crash machine's configuration must flag, the repaired fraction must fit,
# and more RAM with a small reserve must fit without touching the fraction.
hdr "check 19 — GPU budget advisory derives from machine and manifest"
BUD="$PROJ/scripts/gpu-budget.sh"
BM="$WORK/budget-models.yaml"
cat >"$BM" <<'YAML'
defaults: {ttl: 1800, gpu_memory_utilization: 0.85}
models:
  - id: coder
    engine: rapid-mlx
    repo: org/Coder
  - id: rr
    engine: llama-server
    kind: rerank
    group: utilities
    repo: "org/RR:Q8_0"
    ttl: 0
YAML

# Below the minimum (<32GB): the pinned coder does not fit at all — the
# check says so and stops, whatever the manifest holds.
if LOCALAI_MANIFEST="$BM" LOCALAI_MEMSIZE_BYTES=$((16 * 1073741824)) \
   LOCALAI_COTENANT_MB=610 "$BUD" --check >"$WORK/budget-16.txt" 2>&1; then
    bad "16GB passed the budget check — below the minimum must fail"
else
    ok "16GB is flagged as below the minimum"
fi
grep -q 'below the 32GB minimum' "$WORK/budget-16.txt" \
    && ok "the below-minimum flag says so in words" \
    || bad "no below-minimum wording in: $(cat "$WORK/budget-16.txt")"

# Minimum bucket (32GB): a resident co-tenant fails by existing — the message
# names the eviction, not a fraction. Arithmetic said a 610MB resident fit
# with 382MB to spare; the machine answered with 8 aborts in 2 days (#16).
if LOCALAI_MANIFEST="$BM" LOCALAI_MEMSIZE_BYTES=$((32 * 1073741824)) \
   LOCALAI_COTENANT_MB=610 "$BUD" --check >"$WORK/budget.txt" 2>&1; then
    bad "a ttl-0 co-tenant on 32GB passed — the bare bucket must flag residency itself"
else
    ok "32GB with a resident co-tenant is flagged (bare-bucket policy)"
fi
grep -q 'only the budget holder' "$WORK/budget.txt" \
    && ok "the flag says residency is the problem and names the fix" \
    || bad "no eviction guidance in: $(cat "$WORK/budget.txt")"

# The repair on 32GB is making the co-tenant on-demand, not lowering the
# fraction: the same machine must pass at 0.85 once nothing else resides.
BM2="$WORK/budget-models-2.yaml"
sed 's/group: utilities/group: heavy/; s/ttl: 0$/ttl: 120/' "$BM" >"$BM2"
LOCALAI_MANIFEST="$BM2" LOCALAI_MEMSIZE_BYTES=$((32 * 1073741824)) \
   "$BUD" --check >/dev/null 2>&1 \
    && ok "the same machine passes at 0.85 once the co-tenant is on-demand" \
    || bad "on-demand co-tenant still flagged on 32GB"

# Mid bucket (33-36GB): the resident set is gated by arithmetic — too big is
# flagged with the largest fraction that fits, small fits at 0.85.
if LOCALAI_MANIFEST="$BM" LOCALAI_MEMSIZE_BYTES=$((36 * 1073741824)) \
   LOCALAI_COTENANT_MB=3300 "$BUD" --check >"$WORK/budget-36.txt" 2>&1; then
    bad "36GB at 0.85 with 3.3GB of residents passed — the mid bucket must gate arithmetic"
else
    ok "36GB with a 3.3GB resident set is flagged short (mid-bucket arithmetic)"
fi
grep -qE 'fraction <= 0\.[0-9]+' "$WORK/budget-36.txt" \
    && ok "the mid-bucket flag names the largest fraction that fits" \
    || bad "no usable suggestion in: $(cat "$WORK/budget-36.txt")"
if LOCALAI_MANIFEST="$BM" LOCALAI_MEMSIZE_BYTES=$((36 * 1073741824)) \
   LOCALAI_COTENANT_MB=600 LOCALAI_HEAVY_MB=3238 "$BUD" --check >/dev/null 2>&1; then
    ok "36GB: a 600MB resident set fits at 0.85; the heavy member does not gate"
else
    bad "the heavy member gated the mid bucket — overlap is reported, not gating"
fi
LOCALAI_MANIFEST="$BM" LOCALAI_MEMSIZE_BYTES=$((36 * 1073741824)) \
   LOCALAI_COTENANT_MB=600 LOCALAI_HEAVY_MB=3238 "$BUD" >"$WORK/budget-explain.txt" 2>&1
grep -q 'heavy overlap' "$WORK/budget-explain.txt" \
    && ok "explain mode prints the heavy-overlap arithmetic" \
    || bad "explain mode lost the overlap scenario: $(cat "$WORK/budget-explain.txt")"

# Comfortable bucket (>36GB): the full stack gates — with room it passes,
# and a heavy member that overflows the margin is flagged.
LOCALAI_MANIFEST="$BM" LOCALAI_MEMSIZE_BYTES=$((64 * 1073741824)) \
   LOCALAI_COTENANT_MB=1500 "$BUD" --check >/dev/null 2>&1 \
    && ok "64GB with a 1.5GB resident set fits at 0.85" \
    || bad "64GB with a 1.5GB resident set flagged short — margin should scale with RAM"
if LOCALAI_MANIFEST="$BM" LOCALAI_MEMSIZE_BYTES=$((64 * 1073741824)) \
   LOCALAI_COTENANT_MB=1500 LOCALAI_HEAVY_MB=3238 "$BUD" --check >/dev/null 2>&1; then
    bad "64GB passed with the full stack over the margin — comfortable must gate everything"
else
    ok "64GB gates the full stack: residents + heavy + spike over the margin is flagged"
fi

# No rapid-mlx model -> nothing to budget, and never a failure.
BM3="$WORK/budget-models-3.yaml"
cat >"$BM3" <<'YAML'
defaults: {ttl: 1800}
models:
  - id: rr
    engine: llama-server
    kind: rerank
    repo: "org/RR:Q8_0"
YAML
LOCALAI_MANIFEST="$BM3" LOCALAI_MEMSIZE_BYTES=$((32 * 1073741824)) \
   LOCALAI_COTENANT_MB=1500 "$BUD" --check >/dev/null 2>&1 \
    && ok "manifest without a rapid-mlx model budgets nothing and passes" \
    || bad "manifest without a rapid-mlx model failed the check"

hdr "check 20 — the machine report travels alone and buckets correctly"
MR="$PROJ/scripts/machine-report.sh"

# Field-kit stage 0 (PLAN §9): must run on a stock Mac with nothing
# installed — no Homebrew in PATH, no repo tools, bash 3.2 via /bin/bash.
if env -i HOME="$WORK" PATH=/usr/bin:/bin:/usr/sbin "$MR" >"$WORK/mr-self.txt" 2>&1; then
    ok "the report runs with a bare PATH and empty environment"
else
    bad "the report needs something a stock Mac does not have: $(tail -3 "$WORK/mr-self.txt")"
fi
grep -q 'machine report' "$WORK/mr-self.txt" \
    && ok "the report prints its paste-able block" \
    || bad "no report block in: $(cat "$WORK/mr-self.txt")"

# The bucket verdicts mirror gpu-budget.sh's ladder.
LOCALAI_MEMSIZE_BYTES=$((16 * 1073741824)) "$MR" >"$WORK/mr-16.txt" 2>&1
grep -q 'below the 32GB minimum' "$WORK/mr-16.txt" \
    && ok "16GB reports below the minimum" \
    || bad "16GB verdict wrong: $(tail -1 "$WORK/mr-16.txt")"
LOCALAI_MEMSIZE_BYTES=$((32 * 1073741824)) "$MR" >"$WORK/mr-32.txt" 2>&1
grep -q 'only the pinned coder resides' "$WORK/mr-32.txt" \
    && ok "32GB reports the minimum: coder-only residency" \
    || bad "32GB verdict wrong: $(tail -1 "$WORK/mr-32.txt")"
LOCALAI_MEMSIZE_BYTES=$((48 * 1073741824)) "$MR" >"$WORK/mr-48.txt" 2>&1
grep -q 'comfortable' "$WORK/mr-48.txt" \
    && ok "48GB reports the comfortable bucket" \
    || bad "48GB verdict wrong: $(tail -1 "$WORK/mr-48.txt")"

# Above 32GB the 75% rule binds, not the 8GB reserve. The report once used the
# reserve rule alone and overstated the target on exactly the machines the
# field kit visits (48GB: 40960 instead of 36864); keep it agreeing with
# gpu-wired-limit.sh --target.
grep -q 'would set 36864 MB' "$WORK/mr-48.txt" \
    && ok "48GB wired-limit target matches gpu-wired-limit.sh (75% rule binds)" \
    || bad "48GB wired-limit target diverged from gpu-wired-limit.sh: $(grep 'would set' "$WORK/mr-48.txt")"

hdr "check 21 — uninstall.sh removes what is ours, keeps what is theirs"
UH="$WORK/home-uninstall"
UN="$PROJ/scripts/uninstall.sh"

plant_uninstall_fixture() {
    rm -rf "$UH"
    mkdir -p "$UH/Library/LaunchAgents" "$UH/Library/Logs" \
             "$UH/.config/llama-swap" "$UH/.pi/agent/extensions" \
             "$UH/.rapid-mlx/venv/bin" "$UH/.local/bin" \
             "$UH/.cache/rapid-mlx/prefix_cache" \
             "$UH/hf/hub/models--froggeric--Qwen3.6-27B-MLX-4bit" \
             "$UH/hf/hub/models--someone--Unrelated"
    echo plist > "$UH/Library/LaunchAgents/com.llamaswap.server.plist"
    echo plist > "$UH/Library/LaunchAgents/com.llamaswap.server.plist.20260101-000000.bak"
    echo cfg   > "$UH/.config/llama-swap/config.yaml"
    printf '{"providers":{"local":{"baseUrl":"x"},"other":{"baseUrl":"y"}},"_generated_by":"local-ai-setup"}\n' \
        > "$UH/.pi/agent/models.json"
    echo ours    > "$UH/.pi/agent/extensions/project-search.ts"
    echo ours    > "$UH/.pi/agent/extensions/permission-gate.ts"
    echo theirs  > "$UH/.pi/agent/extensions/foreign.ts"
    echo venv    > "$UH/.rapid-mlx/venv/bin/rapid-mlx"
    ln -s "$UH/.rapid-mlx/venv/bin/rapid-mlx" "$UH/.local/bin/rapid-mlx"
    echo w > "$UH/hf/hub/models--froggeric--Qwen3.6-27B-MLX-4bit/weights"
    echo w > "$UH/hf/hub/models--someone--Unrelated/weights"
    echo log > "$UH/Library/Logs/llama-swap.log"
}

# brew shim: answers list/tap queries, logs mutations, touches nothing real.
# The sleep mid-list reproduces the real brew's slow incremental output: piped
# straight into `grep -q` under pipefail, the match exits grep during the
# pause and the next write dies on SIGPIPE, silently skipping the formula —
# the 2026-08-07 field bug. The single-capture fix is immune; this shim keeps
# it that way.
UB="$WORK/uninstall-bin"
mkdir -p "$UB"
cat > "$UB/brew" <<SH
#!/bin/bash
case "\$1" in
    list) printf 'jq\n'; sleep 0.3; printf 'rtk\n' ;;
    tap)  [ \$# -eq 1 ] && printf 'mostlygeek/llama-swap\n' ;;
    uninstall|untap) echo "\$@" >> "$WORK/uninstall-brew.log" ;;
esac
exit 0
SH
chmod +x "$UB/brew"

# Dry run: exit 0, names targets, changes nothing.
plant_uninstall_fixture
if LOCALAI_SKIP_LAUNCHCTL=1 HOME="$UH" HF_HUB_CACHE="$UH/hf/hub" PATH="$UB:$PATH" \
   "$UN" --dry-run > "$WORK/uninstall-dry.txt" 2>&1; then
    ok "uninstall --dry-run exits 0"
else
    bad "uninstall --dry-run failed: $(tail -3 "$WORK/uninstall-dry.txt")"
fi
[ -f "$UH/Library/LaunchAgents/com.llamaswap.server.plist" ] &&
[ -d "$UH/.rapid-mlx" ] && [ -f "$UH/.config/llama-swap/config.yaml" ] \
    && ok "dry run left every fixture file in place" \
    || bad "dry run deleted something"
grep -q 'dry run — nothing was changed' "$WORK/uninstall-dry.txt" \
    && ok "dry run says so" || bad "no dry-run banner"

# Real run, with provenance: probe-added formula/tap are uninstalled through
# the shim; a repo marked absent is removed; foreign things survive.
plant_uninstall_fixture
cat > "$WORK/uninstall-prov.txt" <<PROV
# field-kit provenance v1
formula jq absent
tap mostlygeek/llama-swap absent
hfrepo froggeric/Qwen3.6-27B-MLX-4bit absent
dir $UH/.pi present
PROV
: > "$WORK/uninstall-brew.log"
if LOCALAI_SKIP_LAUNCHCTL=1 HOME="$UH" HF_HUB_CACHE="$UH/hf/hub" PATH="$UB:$PATH" \
   "$UN" --provenance "$WORK/uninstall-prov.txt" > "$WORK/uninstall-real.txt" 2>&1; then
    ok "uninstall (provenance) exits 0"
else
    bad "uninstall (provenance) failed: $(tail -3 "$WORK/uninstall-real.txt")"
fi
[ ! -f "$UH/Library/LaunchAgents/com.llamaswap.server.plist" ] &&
[ ! -d "$UH/.config/llama-swap" ] && [ ! -d "$UH/.rapid-mlx" ] &&
[ ! -e "$UH/.local/bin/rapid-mlx" ] && [ ! -d "$UH/.cache/rapid-mlx" ] \
    && ok "stack-owned artifacts removed" \
    || bad "a stack-owned artifact survived"
[ ! -d "$UH/hf/hub/models--froggeric--Qwen3.6-27B-MLX-4bit" ] &&
[ -d "$UH/hf/hub/models--someone--Unrelated" ] \
    && ok "manifest weights removed, unrelated cache kept" \
    || bad "weights handling wrong"
[ -f "$UH/.pi/agent/extensions/foreign.ts" ] &&
[ ! -f "$UH/.pi/agent/extensions/project-search.ts" ] &&
[ ! -f "$UH/.pi/agent/extensions/permission-gate.ts" ] \
    && ok "our extensions removed, foreign extension kept" \
    || bad "extension handling wrong"
jq -e '.providers.other and (.providers.local | not) and (._generated_by | not)' \
    "$UH/.pi/agent/models.json" >/dev/null 2>&1 \
    && ok "models.json keeps the foreign provider, drops the generated one" \
    || bad "models.json handling wrong: $(cat "$UH/.pi/agent/models.json" 2>/dev/null)"
grep -q '^uninstall jq$' "$WORK/uninstall-brew.log" &&
grep -q '^untap mostlygeek/llama-swap$' "$WORK/uninstall-brew.log" &&
! grep -q 'rtk' "$WORK/uninstall-brew.log" \
    && ok "brew removes only what provenance marks probe-added" \
    || bad "brew calls wrong: $(cat "$WORK/uninstall-brew.log")"

# ── 3 is a live-machine check; noted here for the mapping ────────────────────
printf '\n  \033[2mNOTE\033[0m  check 3 (double-run idempotency) needs the real machine:\n'
printf '        \033[2m./setup.sh && ./setup.sh   # second run must be [ok]/[skip]/[manual] only\033[0m\n'

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
