#!/usr/bin/env bash
#
# local-ai-setup — idempotent installer for a local LLM stack on Apple Silicon.
#
# Re-running on a fully configured machine changes nothing and reads as an audit.
# Every item reports exactly one of:
#
#   [ok]      already in the desired state
#   [install] something was installed
#   [patch]   a file's contents were corrected
#   [change]  a generated config replaced a different live one
#   [skip]    deliberately not done (e.g. lazy downloads)
#   [manual]  needs sudo — the command is printed, never run
#   [fail]    something went wrong; counted, and the run exits non-zero
#
# Usage: ./setup.sh [--dry-run] [--only a,b] [--verify] [-h]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$ROOT/models.yaml"
STATE_DIR="$ROOT/.state"
STAMP="$(date +%Y%m%d-%H%M%S)"

DRY_RUN=0
ONLY=""
# VERIFY is read by the sourced step scripts, which shellcheck cannot follow
# through the dynamic `.` in the step loop.
# shellcheck disable=SC2034
VERIFY=0

ALL_STEPS="tools engines models configs extensions service"

# ── counters ─────────────────────────────────────────────────────────────────
N_OK=0; N_INSTALL=0; N_PATCH=0; N_CHANGE=0; N_SKIP=0; N_MANUAL=0; N_FAIL=0

# ── output ───────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_DIM=$'\033[2m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
    C_BLU=$'\033[34m'; C_RED=$'\033[31m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
    C_DIM=""; C_GRN=""; C_YEL=""; C_BLU=""; C_RED=""; C_BLD=""; C_RST=""
fi

section() { printf '\n%s== %s%s\n' "$C_BLD" "$1" "$C_RST"; }
emit()    { printf '  %s%-9s%s %s\n' "$2" "$1" "$C_RST" "$3"; }

ok()      { N_OK=$((N_OK + 1));         emit "[ok]"      "$C_GRN" "$1"; }
skip()    { N_SKIP=$((N_SKIP + 1));     emit "[skip]"    "$C_DIM" "$1"; }
fail()    { N_FAIL=$((N_FAIL + 1));     emit "[fail]"    "$C_RED" "$1"; }
note()    { printf '            %s%s%s\n' "$C_DIM" "$1" "$C_RST"; }

# manual <label> <command line...> — prints a ready-to-paste command, never runs it.
manual() {
    local label="$1"; shift
    N_MANUAL=$((N_MANUAL + 1))
    emit "[manual]" "$C_YEL" "$label"
    local line
    for line in "$@"; do printf '            %s%s%s\n' "$C_YEL" "$line" "$C_RST"; done
}

# _act <tag> <colour> <counter-name> <label> <cmd...>
# Runs cmd unless --dry-run. Complex work should be wrapped in a shell function
# and passed by name, since arguments are exec'd directly (no shell parsing).
_act() {
    local tag="$1" colour="$2" counter="$3" label="$4"; shift 4
    case "$counter" in
        install) N_INSTALL=$((N_INSTALL + 1)) ;;
        patch)   N_PATCH=$((N_PATCH + 1)) ;;
        change)  N_CHANGE=$((N_CHANGE + 1)) ;;
    esac
    if [ "$DRY_RUN" -eq 1 ]; then
        emit "$tag" "$colour" "$label $C_DIM(dry-run)$C_RST"
    else
        emit "$tag" "$colour" "$label"
        "$@"
    fi
}

do_install() { _act "[install]" "$C_BLU" install "$@"; }
do_patch()   { _act "[patch]"   "$C_YEL" patch   "$@"; }
do_change()  { _act "[change]"  "$C_YEL" change  "$@"; }

is_dry() { [ "$DRY_RUN" -eq 1 ]; }

# ── small helpers ────────────────────────────────────────────────────────────
have()     { command -v "$1" >/dev/null 2>&1; }
brew_has() { brew list --formula "$1" >/dev/null 2>&1; }

# backup_file <path> — timestamped .bak beside the original, if it exists.
#
# Pruned to the most recent BACKUP_KEEP. config.yaml and models.json are
# generated, so every [change] used to drop another copy next to them and none
# were ever removed; the useful one is always the last, and a directory of
# thirty is just noise in the place someone looks when something is wrong.
BACKUP_KEEP=3
backup_file() {
    [ -e "$1" ] || return 0
    cp -p "$1" "$1.$STAMP.bak"
    # STAMP is YYYYMMDD-HHMMSS, so sorting the names in reverse is newest-first
    # and does not depend on mtime surviving a copy.
    local stale _b
    stale="$(for _b in "$1".*.bak; do [ -e "$_b" ] && printf '%s\n' "$_b"; done \
             | sort -r | tail -n +$((BACKUP_KEEP + 1)))"
    [ -n "$stale" ] && printf '%s\n' "$stale" | while IFS= read -r _b; do
        [ -n "$_b" ] && rm -f "$_b"
    done
    return 0
}

sha_of() { [ -f "$1" ] && shasum -a 256 "$1" | cut -d' ' -f1 || echo "-"; }

# hf_hub_root — the HuggingFace hub cache directory, using the same precedence
# huggingface_hub itself does (HF_HUB_CACHE beats HF_HOME). Shared by the models
# step, which downloads into it, and the service step, which must tell the
# engines the same answer: a custom cache used to mean weights landing in one
# place and an engine booting to look in another.
hf_hub_root() {
    printf '%s\n' "${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
}

die() { printf '%serror:%s %s\n' "$C_RED" "$C_RST" "$1" >&2; exit 1; }

# ── manifest access ──────────────────────────────────────────────────────────
# A model is enabled unless it says otherwise. `.enabled // true` would be wrong:
# yq's alternative operator also fires on `false`, which is the value we care about.
manifest_ids() {
    yq -r '.models[] | select(.enabled != false) | .id' "$MANIFEST"
}

# mval <id> <expr> <default> — read one field of one model.
# `//` is safe for numeric defaults here: it fires on null/false but not on 0,
# so `ttl: 0` (never unload) survives.
mval() {
    yq -r ".models[] | select(.id == \"$1\") | ($2 // \"$3\")" "$MANIFEST"
}

defval() { yq -r ".defaults.$1 // \"$2\"" "$MANIFEST"; }

# ── argument parsing ─────────────────────────────────────────────────────────
usage() {
    cat <<'EOF'
local-ai-setup — install and configure the local LLM stack (idempotent).

Usage: ./setup.sh [options]

  --dry-run        Print the planned actions and touch nothing.
  --only <steps>   Comma-separated subset of:
                   tools,engines,models,configs,extensions,service
  --verify         Checksum downloaded model snapshots with `hf cache verify`.
                   Slow (checksums every weight file) — opt-in on purpose.
  -h, --help       This message.

models.yaml is the single source of truth. The llama-swap config and Pi's
models.json are generated from it — never hand-edit the generated files.
EOF
}

# SC2034: VERIFY is consumed by steps/30-models.sh, which shellcheck cannot
# reach through the dynamic source.
# shellcheck disable=SC2034
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)  DRY_RUN=1 ;;
        --verify)   VERIFY=1 ;;
        --only)     [ $# -ge 2 ] || die "--only needs a value"; ONLY="$2"; shift ;;
        --only=*)   ONLY="${1#--only=}" ;;
        -h|--help)  usage; exit 0 ;;
        *)          usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# Resolve which steps run.
if [ -n "$ONLY" ]; then
    STEPS="$(printf '%s' "$ONLY" | tr ',' ' ')"
    for s in $STEPS; do
        case " $ALL_STEPS " in
            *" $s "*) ;;
            *) die "unknown step '$s' (valid: $(printf '%s' "$ALL_STEPS" | tr ' ' ','))" ;;
        esac
    done
else
    STEPS="$ALL_STEPS"
fi

step_file() {
    case "$1" in
        tools)      echo "10-tools.sh" ;;
        engines)    echo "20-engines.sh" ;;
        models)     echo "30-models.sh" ;;
        configs)    echo "40-configs.sh" ;;
        extensions) echo "45-extensions.sh" ;;
        service)    echo "50-service.sh" ;;
    esac
}

# ── preflight ────────────────────────────────────────────────────────────────
[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"
have brew || die "Homebrew is required — https://brew.sh"

# Two different programs are called `yq`. This repo needs mikefarah's Go one;
# the Python `yq` (a jq wrapper) takes different arguments and would fail in
# confusing ways deep inside a step rather than here.
if have yq && ! yq --version 2>&1 | grep -q 'mikefarah'; then
    die "the installed 'yq' is not mikefarah's Go version ($(yq --version 2>&1 | head -1))
       this repo needs https://github.com/mikefarah/yq — 'brew install yq'
       if you need the Python one too, keep it under a different name"
fi

mkdir -p "$STATE_DIR"

if is_dry; then
    _banner="dry run — nothing will be changed"
else
    _banner="$(date '+%Y-%m-%d %H:%M')"
fi
printf '%slocal-ai-setup%s  %s\n' "$C_BLD" "$C_RST" "$_banner"
printf '%smanifest: %s%s\n' "$C_DIM" "$MANIFEST" "$C_RST"

# Steps after `tools` parse the manifest; without yq they cannot.
if ! have yq; then
    case " $STEPS " in
        *" models "*|*" configs "*)
            if is_dry; then
                note "yq is not installed yet — model/config planning will be limited"
            fi ;;
    esac
fi

# ── run ──────────────────────────────────────────────────────────────────────
for s in $STEPS; do
    f="$ROOT/steps/$(step_file "$s")"
    [ -f "$f" ] || die "missing step script: $f"
    # shellcheck source=/dev/null
    . "$f"
done

# ── summary ──────────────────────────────────────────────────────────────────
printf '\n%s== summary%s\n' "$C_BLD" "$C_RST"
printf '  ok %d · installed %d · patched %d · changed %d · skipped %d · manual %d' \
       "$N_OK" "$N_INSTALL" "$N_PATCH" "$N_CHANGE" "$N_SKIP" "$N_MANUAL"
[ "$N_FAIL" -gt 0 ] && printf ' · %sfailed %d%s' "$C_RED" "$N_FAIL" "$C_RST"
printf '\n'

if is_dry; then
    printf '%s  dry run — nothing was changed.%s\n' "$C_DIM" "$C_RST"
elif [ "$N_MANUAL" -gt 0 ]; then
    printf '%s  %d item(s) need sudo — the commands above are ready to paste.%s\n' \
           "$C_DIM" "$N_MANUAL" "$C_RST"
fi

[ "$N_FAIL" -eq 0 ] || exit 1
exit 0
