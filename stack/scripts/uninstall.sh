#!/usr/bin/env bash
#
# Uninstall the local-ai-setup stack — the README removal table, executable.
#
#   scripts/uninstall.sh [--dry-run] [--provenance FILE]
#
# Removes what the stack owns outright (service, generated configs, the
# rapid-mlx venv, this manifest's model weights, logs) and is conservative
# about everything shared: brew formulas and the tap are PRINTED as commands
# unless a provenance file proves the probe installed them, and files under
# /etc are always printed as sudo commands, never executed (ground rule 3).
#
# Provenance (written by the field kit before it installs anything) is one
# line per item, `<kind> <name> <present|absent>`:
#
#   # field-kit provenance v1
#   formula ripgrep present
#   tap mostlygeek/llama-swap absent
#   dir /Users/friend/.pi absent
#   hfrepo froggeric/Qwen3.6-27B-MLX-4bit absent
#
# "absent" means the item did not exist before the probe — so removing it
# restores the machine. "present" (or no line at all) protects it: shared
# items are printed, stack-owned files are still removed. Without a
# provenance file the script behaves as if every shared item were present.
#
# LOCALAI_SKIP_LAUNCHCTL=1 skips the bootout (the hermetic suite and the
# field kit's foreground-serve session both run with nothing loaded — and a
# bootout from a test would hit the REAL service, the check-16 lesson).
#
# Never run with sudo. Exit 0 unless a removal actually failed.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${LOCALAI_MANIFEST:-$ROOT/models.yaml}"
PLIST_LABEL="com.llamaswap.server"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

DRY_RUN=0
PROV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --provenance) PROV="${2:?--provenance needs a file}"; shift ;;
        --provenance=*) PROV="${1#*=}" ;;
        -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 1 ;;
    esac
    shift
done
[ -z "$PROV" ] || [ -f "$PROV" ] || { printf 'provenance file not found: %s\n' "$PROV" >&2; exit 1; }

if [ -t 1 ]; then
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_RED=""; C_GRN=""; C_YEL=""; C_DIM=""; C_OFF=""
fi

N_REMOVED=0; N_KEPT=0; N_MANUAL=0; N_FAIL=0
removed() { printf '  %s[remove]%s  %s\n' "$C_GRN" "$C_OFF" "$1"; N_REMOVED=$((N_REMOVED + 1)); }
kept()    { printf '  %s[keep]%s    %s\n' "$C_DIM" "$C_OFF" "$1"; N_KEPT=$((N_KEPT + 1)); }
manual()  { printf '  %s[manual]%s  %s\n' "$C_YEL" "$C_OFF" "$1"; shift
            for _c in "$@"; do printf '            %s\n' "$_c"; done
            N_MANUAL=$((N_MANUAL + 1)); }
failed()  { printf '  %s[fail]%s    %s\n' "$C_RED" "$C_OFF" "$1"; N_FAIL=$((N_FAIL + 1)); }
note()    { printf '  %s%s%s\n' "$C_DIM" "$1" "$C_OFF"; }
section() { printf '\n%s== %s%s\n' "$C_DIM" "$1" "$C_OFF"; }

is_dry() { [ "$DRY_RUN" = "1" ]; }

# The only mutation primitive. Mirrors setup.sh's _act: count first, then
# either narrate (dry-run) or execute.
do_remove() { # $1 = label, rest = command
    local _label="$1"; shift
    if is_dry; then
        printf '  %s[remove]%s  %s %s(dry-run)%s\n' "$C_GRN" "$C_OFF" "$_label" "$C_DIM" "$C_OFF"
        N_REMOVED=$((N_REMOVED + 1))
        return 0
    fi
    if "$@"; then removed "$_label"; else failed "$_label"; fi
}

# provenance: was this item absent before the probe (i.e. probe-added)?
prov_added() { # $1 = kind, $2 = name
    [ -n "$PROV" ] || return 1
    grep -q "^$1 $2 absent\$" "$PROV" 2>/dev/null
}

hf_hub_root() {
    printf '%s' "${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
}

# ── service ──────────────────────────────────────────────────────────────────
section "service"
if [ "${LOCALAI_SKIP_LAUNCHCTL:-0}" = "1" ]; then
    note "launchctl skipped (LOCALAI_SKIP_LAUNCHCTL=1)"
elif launchctl list 2>/dev/null | grep -q "	$PLIST_LABEL\$"; then
    do_remove "bootout gui/$(id -u)/$PLIST_LABEL" \
        launchctl bootout "gui/$(id -u)/$PLIST_LABEL"
else
    note "$PLIST_LABEL not loaded"
fi
for f in "$PLIST_DST" "$PLIST_DST".*.bak; do
    [ -e "$f" ] || continue
    do_remove "$f" rm -f "$f"
done

# ── generated configs ────────────────────────────────────────────────────────
section "generated configs"
SWAP_DIR="$HOME/.config/llama-swap"
if [ -d "$SWAP_DIR" ]; then
    do_remove "$SWAP_DIR/" rm -rf "$SWAP_DIR"
else
    note "no $SWAP_DIR"
fi

PI_MODELS="$HOME/.pi/agent/models.json"
strip_local_provider() {
    local tmp
    tmp="$(mktemp)"
    jq 'del(.providers.local) | del(._generated_by)' "$PI_MODELS" > "$tmp" && mv "$tmp" "$PI_MODELS"
}
if [ -f "$PI_MODELS" ]; then
    if ! command -v jq >/dev/null 2>&1; then
        failed "$PI_MODELS — need jq to edit it safely; not touching it"
    elif [ "$(jq -r '.providers | del(.local) | length' "$PI_MODELS" 2>/dev/null)" = "0" ]; then
        do_remove "$PI_MODELS (no other providers)" rm -f "$PI_MODELS"
    else
        do_remove "$PI_MODELS — drop the generated 'local' provider, keep the rest" \
            strip_local_provider
    fi
    for f in "$PI_MODELS".*.bak; do
        [ -e "$f" ] || continue
        do_remove "$f" rm -f "$f"
    done
else
    note "no $PI_MODELS"
fi

# ── Pi extensions ────────────────────────────────────────────────────────────
# Only the files this repo installs (pi-extensions/*.ts + the permission gate
# fetched by the engines step). Pi's own state is not ours to delete — unless
# provenance proves ~/.pi did not exist before the probe.
section "Pi extensions"
PI_EXT_DIR="$HOME/.pi/agent/extensions"
if prov_added dir "$HOME/.pi"; then
    if [ -d "$HOME/.pi" ]; then
        do_remove "$HOME/.pi/ (probe-added per provenance)" rm -rf "$HOME/.pi"
    else
        note "no $HOME/.pi"
    fi
elif [ -d "$PI_EXT_DIR" ]; then
    _ext_names="permission-gate.ts"
    for _src in "$ROOT"/pi-extensions/*.ts; do
        [ -f "$_src" ] || continue
        _ext_names="$_ext_names $(basename "$_src")"
    done
    for _name in $_ext_names; do
        for f in "$PI_EXT_DIR/$_name" "$PI_EXT_DIR/$_name".*.bak; do
            [ -e "$f" ] || continue
            do_remove "$f" rm -f "$f"
        done
    done
    note "the rest of ~/.pi is Pi's (sessions, settings) — left in place"
else
    note "no $PI_EXT_DIR"
fi

# ── rapid-mlx venv + engine caches ───────────────────────────────────────────
section "rapid-mlx"
if [ -d "$HOME/.rapid-mlx" ]; then
    do_remove "$HOME/.rapid-mlx/" rm -rf "$HOME/.rapid-mlx"
else
    note "no $HOME/.rapid-mlx"
fi
if [ -d "$HOME/.local/bin" ]; then
    # shellcheck disable=SC2039  # -lname is BSD find on macOS, fine here
    _links="$(find "$HOME/.local/bin" -maxdepth 1 -type l -lname "$HOME/.rapid-mlx/*" 2>/dev/null || true)"
    if [ -n "$_links" ]; then
        printf '%s\n' "$_links" | while IFS= read -r l; do
            do_remove "$l (symlink into the venv)" rm -f "$l"
        done
    fi
fi
if [ -d "$HOME/.cache/rapid-mlx" ]; then
    do_remove "$HOME/.cache/rapid-mlx/ (prefix cache, kv checkpoints)" rm -rf "$HOME/.cache/rapid-mlx"
fi

# ── model weights ────────────────────────────────────────────────────────────
# Every cached repo the manifest references, by name, wherever it is named
# (repo: fields and flags alike). Unrecognized repos are somebody else's and
# stay. `models--{org}--{name}`: the first `--` after the prefix separates
# org from name; neither may contain a double dash.
section "model weights ($(hf_hub_root))"
HUB="$(hf_hub_root)"
if [ -d "$HUB" ] && [ -f "$MANIFEST" ]; then
    for d in "$HUB"/models--*; do
        [ -d "$d" ] || continue
        _id="$(basename "$d" | sed 's/^models--//; s/--/\//')"
        if ! grep -qF "$_id" "$MANIFEST"; then
            kept "$d — not referenced by $MANIFEST"
        elif prov_added hfrepo "$_id" || [ -z "$PROV" ]; then
            _sz="$(du -sh "$d" 2>/dev/null | cut -f1)"
            do_remove "$d (${_sz:-?})" rm -rf "$d"
        else
            kept "$d — pre-existing per provenance"
        fi
    done
elif [ ! -f "$MANIFEST" ]; then
    failed "manifest not found at $MANIFEST — cannot tell which weights are the stack's"
else
    note "no HF cache at $HUB"
fi

# ── logs ─────────────────────────────────────────────────────────────────────
section "logs"
for f in "$HOME/Library/Logs/llama-swap.log"* "$HOME/Library/Logs/llama-swap.error.log"*; do
    [ -e "$f" ] || continue
    do_remove "$f" rm -f "$f"
done

# ── files under /etc (sudo — printed, never executed) ────────────────────────
section "sudo-written files (printed only — ground rule 3)"
if [ -f /etc/newsyslog.d/llama-swap.conf ]; then
    manual "log rotation config" \
        "sudo rm /etc/newsyslog.d/llama-swap.conf"
fi
if grep -q '^iogpu.wired_limit_mb=' /etc/sysctl.conf 2>/dev/null; then
    manual "GPU wired limit (persisted + live reset; 0 = macOS default)" \
        "sudo sed -i '' '/^iogpu.wired_limit_mb=/d' /etc/sysctl.conf" \
        "sudo sysctl -w iogpu.wired_limit_mb=0"
fi

# ── Homebrew (last — earlier sections still needed jq/yq) ────────────────────
section "Homebrew"
BREW_ITEMS="
formula:llama-swap
formula:llama.cpp
formula:hf
formula:uv
formula:pi-coding-agent
formula:ripgrep
formula:fd
formula:jq
formula:yq
formula:ast-grep
formula:gh
formula:difftastic
formula:bat
formula:tokei
formula:fzf
formula:rtk
tap:mostlygeek/llama-swap
"
if ! command -v brew >/dev/null 2>&1; then
    note "no brew on PATH — nothing to do"
else
    # Capture both lists ONCE. Piping brew straight into `grep -q` under
    # pipefail is a silent skip: grep exits at the first match, brew takes a
    # SIGPIPE mid-write, and `|| continue` drops the item — which formulas
    # survive depends on their alphabetical position in a 200-formula list.
    _formula_list="$(brew list --formula 2>/dev/null || true)"
    _tap_list="$(brew tap 2>/dev/null || true)"
    _print_cmds=""
    for _entry in $BREW_ITEMS; do
        _kind="${_entry%%:*}"
        _name="${_entry##*:}"
        if [ "$_kind" = "tap" ]; then
            printf '%s\n' "$_tap_list" | grep -Fqx "$_name" || continue
            _cmd="brew untap $_name"
        else
            printf '%s\n' "$_formula_list" | grep -Fqx "${_name##*/}" || continue
            _cmd="brew uninstall $_name"
        fi
        if prov_added "$_kind" "$_name"; then
            # shellcheck disable=SC2086
            do_remove "$_cmd (probe-added per provenance)" $_cmd
        else
            _print_cmds="$_print_cmds|$_cmd"
        fi
    done
    if [ -n "$_print_cmds" ]; then
        note "these are shared tools — remove only what nothing else on this machine uses:"
        printf '%s\n' "$_print_cmds" | tr '|' '\n' | sed '/^$/d; s/^/            /'
        N_MANUAL=$((N_MANUAL + 1))
    fi
fi

# ── summary ──────────────────────────────────────────────────────────────────
printf '\n== summary\n'
printf '  removed %d · kept %d · manual %d' "$N_REMOVED" "$N_KEPT" "$N_MANUAL"
[ "$N_FAIL" -gt 0 ] && printf ' · %sfailed %d%s' "$C_RED" "$N_FAIL" "$C_OFF"
printf '\n'
is_dry && printf '  %sdry run — nothing was changed%s\n' "$C_DIM" "$C_OFF"
note "the repo clone itself (with .state/) is removed by deleting its directory"
[ "$N_FAIL" -eq 0 ]
