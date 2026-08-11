#!/usr/bin/env bash
#
# Raise the Metal wired-memory ceiling, and persist it across reboots.
#
#   scripts/gpu-wired-limit.sh --dry-run    # show the plan, touch nothing
#   scripts/gpu-wired-limit.sh --check      # exit 0 if applied and persisted
#   scripts/gpu-wired-limit.sh --target     # print the computed target only
#   sudo scripts/gpu-wired-limit.sh         # apply
#
# macOS caps GPU-wired memory well below physical RAM — about 21GB on a 32GB
# machine. That is less than the pinned coder + utilities + one heavy model need
# together, so loading a heavy model evicts the very thing the pinned design
# exists to protect.
#
# The target is computed from this machine's RAM rather than hardcoded, so the
# script is correct on a Mac with a different amount of memory.

set -euo pipefail

SYSCTL_KEY="iogpu.wired_limit_mb"
SYSCTL_CONF="/etc/sysctl.conf"

die() { printf 'error: %s\n' "$1" >&2; exit 2; }

# Target = 75% of physical RAM, but always leave at least 8GB for the system,
# rounded down to a whole GB. On 32GB both rules land on 24576MB, which is the
# value every measurement in docs/FINDINGS.md was taken against. The reserve is
# what stops the 75% rule from starving a 16GB machine.
#
# LOCALAI_MEMSIZE_BYTES overrides the probe so the formula can be tested against
# machine sizes this laptop does not have.
compute_target() {
    local bytes mb by_pct by_reserve target
    bytes="${LOCALAI_MEMSIZE_BYTES:-$(sysctl -n hw.memsize)}"
    mb=$(( bytes / 1048576 ))
    by_pct=$(( mb * 3 / 4 ))
    by_reserve=$(( mb - 8192 ))

    target="$by_pct"
    if [ "$by_reserve" -lt "$target" ]; then
        target="$by_reserve"
    fi
    target=$(( target / 1024 * 1024 ))

    if [ "$target" -lt 4096 ]; then
        die "computed target ${target}MB from ${mb}MB of RAM — too small to be right"
    fi
    printf '%s' "$target"
}

runtime_value() { sysctl -n "$SYSCTL_KEY" 2>/dev/null || printf 'unknown'; }

# Last wins if the file somehow has several, which is what sysctl itself does.
persisted_value() {
    [ -f "$SYSCTL_CONF" ] || return 0
    grep -E "^[[:space:]]*${SYSCTL_KEY}=" "$SYSCTL_CONF" 2>/dev/null |
        tail -1 | cut -d= -f2 | tr -d '[:space:]'
}

apply() {
    [ "$(id -u)" -eq 0 ] || die "needs root — run: sudo $0"

    sysctl -w "$SYSCTL_KEY=$TARGET" >/dev/null
    printf 'set %s=%s for this boot\n' "$SYSCTL_KEY" "$TARGET"

    # Rewrite rather than append. `tee -a` would stack a second, contradicting
    # line on every run, and sysctl silently takes the last one.
    local tmp
    tmp="$(mktemp "${TMPDIR:-/tmp}/sysctl-conf.XXXXXX")"
    if [ -f "$SYSCTL_CONF" ]; then
        grep -vE "^[[:space:]]*${SYSCTL_KEY}=" "$SYSCTL_CONF" >"$tmp" || true
    fi
    printf '%s=%s\n' "$SYSCTL_KEY" "$TARGET" >>"$tmp"
    install -m 0644 -o root -g wheel "$tmp" "$SYSCTL_CONF"
    rm -f "$tmp"
    printf 'persisted in %s\n' "$SYSCTL_CONF"
}

TARGET="$(compute_target)"
MODE="apply"
case "${1:-}" in
    --dry-run) MODE="dry-run" ;;
    --check)   MODE="check" ;;
    --target)  printf '%s\n' "$TARGET"; exit 0 ;;
    -h|--help) sed -n '3,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "")        ;;
    *)         die "unknown option: $1" ;;
esac

_runtime="$(runtime_value)"
_persisted="$(persisted_value)"

case "$MODE" in
check)
    if [ "$_runtime" = "$TARGET" ] && [ "$_persisted" = "$TARGET" ]; then
        printf '%s MB (persisted)\n' "$TARGET"
        exit 0
    fi
    if [ "$_runtime" = "$TARGET" ]; then
        printf '%s MB live but not persisted — lost on reboot\n' "$TARGET"
    else
        printf '%s MB, target %s MB\n' "$_runtime" "$TARGET"
    fi
    exit 1
    ;;
dry-run)
    printf 'RAM            %s MB\n' "$(( ${LOCALAI_MEMSIZE_BYTES:-$(sysctl -n hw.memsize)} / 1048576 ))"
    printf 'target         %s MB\n' "$TARGET"
    printf 'runtime now    %s\n' "$_runtime"
    printf 'persisted now  %s\n' "${_persisted:-<absent>}"
    printf '\nwould run:\n  sysctl -w %s=%s\n' "$SYSCTL_KEY" "$TARGET"
    printf '  and set that one line in %s\n' "$SYSCTL_CONF"
    ;;
apply)
    if [ "$_runtime" = "$TARGET" ] && [ "$_persisted" = "$TARGET" ]; then
        printf 'already %s MB and persisted — nothing to do\n' "$TARGET"
        exit 0
    fi
    apply
    ;;
esac
