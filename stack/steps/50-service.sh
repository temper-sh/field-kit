# steps/50-service.sh — the launchd agent, plus the two tweaks that need sudo.
#
# The service runs llama-swap with --watch-config, so config.yaml changes are
# picked up live. Only a changed plist warrants a restart.
#
# Sourced by setup.sh.
# shellcheck shell=bash

section "service"

PLIST_LABEL="com.llamaswap.server"
PLIST_SRC="$ROOT/templates/$PLIST_LABEL.plist.tmpl"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
STATE_RAPIDMLX="$STATE_DIR/rapidmlx_bin"
# Only the error log is referenced here, to point at on a failed health check.
# The rotation script derives both paths itself.
LOG_ERR="$HOME/Library/Logs/llama-swap.error.log"

[ -f "$PLIST_SRC" ] || { fail "missing template: $PLIST_SRC"; return 0; }

# The engine bin dir is recorded by the engines step; fall back to the default.
if [ -f "$STATE_RAPIDMLX" ] && [ -s "$STATE_RAPIDMLX" ]; then
    RAPIDMLX_BIN="$(cat "$STATE_RAPIDMLX")"
else
    RAPIDMLX_BIN="$HOME/.local/bin"
    note "no .state/rapidmlx_bin yet — assuming $RAPIDMLX_BIN"
fi

# Homebrew is at /opt/homebrew on Apple Silicon and /usr/local elsewhere, and
# nothing stops anyone relocating it. Ask brew rather than assuming.
BREW_PREFIX="$(brew --prefix 2>/dev/null || printf '/opt/homebrew')"

# Build the service PATH, skipping entries already present. RAPIDMLX_BIN is
# usually ~/.local/bin itself, and a doubled entry in a generated file is the
# kind of thing that makes people wonder what else is wrong with it.
SERVICE_PATH="$BREW_PREFIX/bin"
add_service_path() {
    case ":$SERVICE_PATH:" in
        *":$1:"*) ;;
        *) SERVICE_PATH="$SERVICE_PATH:$1" ;;
    esac
}
add_service_path "$RAPIDMLX_BIN"
add_service_path "$HOME/.local/bin"
for _p in /usr/bin /bin /usr/sbin /sbin; do add_service_path "$_p"; done

# launchd starts with a bare environment, so the engines would fall back to the
# default cache no matter what the models step was told. Pass the resolved hub
# directory explicitly: weights downloaded to a custom HF_HOME used to be
# invisible to the engine that had to load them.
HF_HUB_ROOT="$(hf_hub_root)"

# rapid-mlx parks KV checkpoints on disk and caps itself at 20 GiB (FINDINGS
# #14 — a cap, not a leak, so it grows straight back after any manual clean).
# 20 GiB is 8% of a 256GB MacBook, taken silently by something nothing told the
# user about. Scale it to what the machine actually has, and never exceed
# upstream's own default.
#
# This is a policy, not a measurement: 10% of free space, clamped to [1, 20]
# GiB. The lower bound is nominal — the manifest needs ~19GB of weights, so a
# machine near it cannot install the stack at all and the cache size is moot.
# LOCALAI_FREE_KB overrides the probe so the formula can be tested against
# disks this machine does not have.
kv_checkpoint_cap_bytes() {
    local free_kb free_gib cap
    free_kb="${LOCALAI_FREE_KB:-$(df -k "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')}"
    case "$free_kb" in
        ''|*[!0-9]*) printf '%s\n' "$((1 * 1073741824))"; return 0 ;;
    esac
    free_gib=$((free_kb / 1048576))
    cap=$((free_gib / 10))
    [ "$cap" -gt 20 ] && cap=20
    [ "$cap" -lt 1 ]  && cap=1
    printf '%s\n' "$((cap * 1073741824))"
}
KV_CAP="$(kv_checkpoint_cap_bytes)"

SERVICE_TMP="$(mktemp "${TMPDIR:-/tmp}/llamaswap-plist.XXXXXX")"
sed -e "s|__HOME__|$HOME|g" \
    -e "s|__BREW_PREFIX__|$BREW_PREFIX|g" \
    -e "s|__HF_HUB_CACHE__|$HF_HUB_ROOT|g" \
    -e "s|__KV_CHECKPOINT_MAX__|$KV_CAP|g" \
    -e "s|__SERVICE_PATH__|$SERVICE_PATH|g" "$PLIST_SRC" >"$SERVICE_TMP"

# LOCALAI_SKIP_LAUNCHCTL=1 renders and installs the plist but never speaks to
# launchd. The hermetic suite needs it because the label is a constant: a
# `launchctl load` from a test running under a scratch HOME does not create a
# second service, it *replaces* the real one with a job pointing into a temp
# directory that is deleted moments later. That is not hypothetical — it
# happened while this very check was being written, and :8080 went down with it.
_skip_launchctl() { [ "${LOCALAI_SKIP_LAUNCHCTL:-0}" = "1" ]; }

service_loaded() {
    _skip_launchctl && return 1
    launchctl list 2>/dev/null | grep -q "	$PLIST_LABEL\$"
}

load_plist() {
    _skip_launchctl && return 0
    launchctl load "$PLIST_DST"
}

install_plist() {
    mkdir -p "$(dirname "$PLIST_DST")"
    if service_loaded; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    backup_file "$PLIST_DST"
    cp "$SERVICE_TMP" "$PLIST_DST"
    load_plist
}

if [ ! -f "$PLIST_DST" ]; then
    do_install "launchd plist" install_plist
elif diff -q "$SERVICE_TMP" "$PLIST_DST" >/dev/null 2>&1; then
    ok "launchd plist"
    if service_loaded; then
        ok "$PLIST_LABEL loaded"
    elif _skip_launchctl; then
        # Under the skip flag "not loaded" is always true (service_loaded is
        # forced false), so loading here would report [change] on every run
        # and break second-run cleanliness for anything driving setup with
        # the flag set — the hermetic suite and the field kit's install trial.
        skip "$PLIST_LABEL not loaded (launchctl skipped)"
    else
        do_change "$PLIST_LABEL not loaded — loading" load_plist
    fi
else
    do_change "launchd plist (reinstall + reload)" install_plist
fi
rm -f "$SERVICE_TMP"

# ── health gate ──────────────────────────────────────────────────────────────
# /v1/models answers as soon as the proxy is up — well before any preload
# finishes — so this is a liveness check, not a readiness one.
health_check() {
    local attempt=0 models
    while [ "$attempt" -lt 30 ]; do
        attempt=$((attempt + 1))
        models="$(curl -fsS -m 2 http://localhost:8080/v1/models 2>/dev/null || true)"
        if [ -n "$models" ]; then
            printf '%s' "$models" | jq -r '.data[].id' 2>/dev/null |
                while IFS= read -r m; do note "serving: $m"; done
            return 0
        fi
        sleep 1
    done
    return 1
}


if is_dry; then
    skip "health check (dry-run)"
elif _skip_launchctl; then
    # Nothing was loaded, so anything answering on :8080 is somebody else's
    # service — checking it would report a pass this run did not earn.
    skip "health check (launchctl skipped)"
elif health_check; then
    ok "llama-swap answering on http://localhost:8080"
else
    fail "llama-swap did not answer on :8080 within 30s — see $LOG_ERR"
fi

# ── system tweaks: reported, never applied ───────────────────────────────────
# Both need sudo. This script does not run sudo; it prints what to paste.
#
# The targets used to be spelled out here, which meant a value measured on a
# 32GB machine was baked into a check that would be wrong on any other one.
# Both now live in scripts/, derive what they need from the running machine,
# and answer --check so this step never has to know the numbers.

# _sudo_tweak <label> <script> <note>...
_sudo_tweak() {
    local label="$1" script="$2"; shift 2
    local status rc=0
    if [ ! -x "$script" ]; then
        fail "$label — missing or not executable: $script"
        return 0
    fi
    status="$("$script" --check 2>&1)" || rc=$?
    case "$rc" in
        0) ok "$label — $status" ;;
        1)
            manual "$label: $status" "sudo $script"
            local line
            for line in "$@"; do note "$line"; done
            ;;
        *) fail "$label — check failed: $status" ;;
    esac
}

_sudo_tweak "GPU wired limit" "$ROOT/scripts/gpu-wired-limit.sh" \
    "lifts the default GPU ceiling so the pinned coder + utilities + one heavy" \
    "model never force an eviction; the target is derived from installed RAM"

_sudo_tweak "log rotation" "$ROOT/scripts/log-rotation.sh" \
    "owner:group is mandatory for user-owned files — without it the rotated" \
    "files are created root-owned and llama-swap can no longer write them"
