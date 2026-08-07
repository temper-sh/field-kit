#!/usr/bin/env bash
#
# Machine report — will this Mac run the stack the field kit installs, and
# in which bucket? Prints one copy-pasteable block; send the whole block
# back to whoever sent you here. Run it BEFORE anything else:
#
#   ./machine-report.sh
#
# SELF-CONTAINED ON PURPOSE: no Homebrew, no yq, no repo checkout — this one
# file runs on a stock Mac (bash 3.2, read-only sysctls, nothing installed,
# nothing written). A "below the minimum" verdict means the full probe has
# nothing to measure yet — stop there, send the block anyway.
#
# DISTRIBUTION COPY. The canonical file lives in the stack repo
# (scripts/machine-report.sh, tested by its offline check 20); the
# constants are hand-duplicated from its gpu-budget.sh and README bucket
# table. Keep all of it in sync by hand — this kit's tests pin the
# wired-limit formula so silent drift fails loudly.
#
# Overridable for the hermetic test suite:
#   LOCALAI_MEMSIZE_BYTES  pretend RAM

set -euo pipefail

DEVICE_MEM_PCT=81           # Metal device memory ~81% of RAM (gpu-budget.sh)
MACOS_DEFAULT_WALL_PCT=65   # the wired-limit wall before gpu-wired-limit.sh
SYSTEM_RESERVE_MB=8192      # gpu-wired-limit.sh's formula: min(75% of RAM,
WIRED_PCT_NUM=3             # RAM minus 8GB), rounded down to a whole GB —
WIRED_PCT_DEN=4             # both rules, or the report overstates >32GB
CODER_MB=15360              # the default manifest's pinned 27B coder at 4-bit
BUCKET_MIN_GB=32
BUCKET_MID_GB=36

ram_bytes="${LOCALAI_MEMSIZE_BYTES:-$(sysctl -n hw.memsize)}"
ram_mb=$(( ram_bytes / 1048576 ))
ram_gb=$(( (ram_mb + 512) / 1024 ))

chip="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || printf 'unknown')"
hw_model="$(sysctl -n hw.model 2>/dev/null || printf 'unknown')"
os_ver="$(sw_vers -productVersion 2>/dev/null || printf 'unknown')"
os_build="$(sw_vers -buildVersion 2>/dev/null || printf '?')"

case "$chip" in
    *M5*) chip_note="same generation as the measurement machine" ;;
    *)    chip_note="not an M5 — every number in the repo was measured on an M5; prefill-based decisions may not transfer" ;;
esac

# The wall today vs. what scripts/gpu-wired-limit.sh would set. With RAM
# overridden the live sysctl belongs to a different machine, so skip it.
if [ -n "${LOCALAI_MEMSIZE_BYTES:-}" ]; then
    wired_now="n/a (RAM overridden for testing)"
else
    _w="$(sysctl -n iogpu.wired_limit_mb 2>/dev/null || printf '0')"
    if [ "$_w" -gt 0 ] 2>/dev/null; then
        wired_now="$_w MB (raised from the macOS default)"
    else
        wired_now="macOS default (~$(( ram_mb * MACOS_DEFAULT_WALL_PCT / 100 )) MB, ${MACOS_DEFAULT_WALL_PCT}% of RAM)"
    fi
fi
by_pct=$(( ram_mb * WIRED_PCT_NUM / WIRED_PCT_DEN ))
by_reserve=$(( ram_mb - SYSTEM_RESERVE_MB ))
target_mb="$by_pct"
[ "$by_reserve" -lt "$target_mb" ] && target_mb="$by_reserve"
target_mb=$(( target_mb / 1024 * 1024 ))
[ "$target_mb" -lt 0 ] && target_mb=0
dev_mb=$(( ram_mb * DEVICE_MEM_PCT / 100 ))

if [ "$ram_gb" -lt "$BUCKET_MIN_GB" ]; then
    bucket="below the ${BUCKET_MIN_GB}GB minimum — the ~$(( CODER_MB / 1024 ))GB pinned coder does not leave a working margin; no drop-in manifest exists yet"
elif [ "$ram_gb" -eq "$BUCKET_MIN_GB" ]; then
    bucket="the minimum: only the pinned coder resides on the GPU; specialists load on demand with a short ttl"
elif [ "$ram_gb" -le "$BUCKET_MID_GB" ]; then
    bucket="a small always-resident set fits beside the coder, gated by arithmetic (scripts/gpu-budget.sh)"
else
    bucket="comfortable — residents plus an on-demand heavy model, gated by arithmetic (scripts/gpu-budget.sh)"
fi

printf 'temper machine report — send this whole block back\n'
printf 'hardware        %s\n' "$hw_model"
printf 'chip            %s   (%s)\n' "$chip" "$chip_note"
printf 'macOS           %s (%s)\n' "$os_ver" "$os_build"
printf 'RAM             %s MB (%sGB)\n' "$ram_mb" "$ram_gb"
printf 'wired limit     %s\n' "$wired_now"
printf '                gpu-wired-limit.sh would set %s MB\n' "$target_mb"
printf 'Metal device    ~%s MB (%s%% of RAM, estimate)\n' "$dev_mb" "$DEVICE_MEM_PCT"
printf 'bucket          %s\n' "$bucket"
