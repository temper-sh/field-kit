#!/usr/bin/env bash
#
# GPU pool budget advisory — does the coder's Metal allocation limit leave
# room for everything else that shares the wired-memory pool?
#
#   scripts/gpu-budget.sh            # print this machine's arithmetic
#   scripts/gpu-budget.sh --check    # exit 0 if the margin covers the reserve,
#                                    # exit 1 with a one-line summary if not
#
# Advisory only: models.yaml is the user's file. This computes and reports,
# never edits.
#
# The failure this guards against is docs/FINDINGS.md #16. rapid-mlx's
# --gpu-memory-utilization is a FRACTION of Metal's device memory (~81% of
# RAM), while the hard wall is iogpu.wired_limit_mb (~75% of RAM once
# scripts/gpu-wired-limit.sh is applied, ~65% before). The margin between the
# two must hold every co-resident llama-server model plus transient spikes —
# costs that are absolute, not proportional, so no single fraction is right
# on every machine. And when the pool runs out, MLX does not fail one
# request: it aborts the whole engine mid-turn.
#
# The gate is BUCKETED by RAM (owner policy, 2026-08-06 — set after 8 aborts
# in 2 days on the 32GB machine, README has the table):
#   <32GB   below the minimum: the pinned ~15GB coder does not leave a
#           working margin at all. The check says so and stops.
#   32GB    the minimum: only the budget holder resides on the GPU. Any
#           llama-server entry that never unloads (ttl 0 / utilities) fails
#           the check by existing — arithmetic said a 610MB resident fit,
#           the machine said otherwise.
#   33-36GB the always-resident set + spike must fit the margin; a heavy
#           on-demand model wired at the same time is reported, not gated.
#   >36GB   comfortable: the full stack — residents + the largest heavy
#           member + spike — must fit.
#
# Overridable probes, so the formula is testable for machines this one is not
# (the gpu-wired-limit.sh precedent):
#   LOCALAI_MEMSIZE_BYTES  RAM           (also switches the wall to the
#                                         computed target — pure arithmetic)
#   LOCALAI_WIRED_MB       the wall itself
#   LOCALAI_COTENANT_MB    always-resident model total, skipping the hf-cache scan
#   LOCALAI_HEAVY_MB       largest on-demand model, skipping the hf-cache scan
#   LOCALAI_MANIFEST       manifest path

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="${LOCALAI_MANIFEST:-$ROOT/models.yaml}"
HF_HUB_ROOT="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"

# Both constants are honest approximations, not measurements to lean on:
#
# DEVICE_MEM_PCT: rapid-mlx multiplies the utilization fraction by Metal's
# recommended working-set size, which it printed as 25.8GB on the 32GB
# machine FINDINGS #16 happened on — 80.6% of RAM, rounded to 81 here.
# Re-derive from the engine's own "Metal memory limits set:
# allocation_limit=…" startup line, which is the number that actually binds.
#
# SPIKE_MB is policy, not measurement (the 50-service.sh disk-cap pattern):
# an allowance for prefill batches and any re-enabled checkpoint writer.
# FINDINGS #16's crashes happened inside a ~2GB total margin.
DEVICE_MEM_PCT=81
SPIKE_MB=1024
MACOS_DEFAULT_WALL_PCT=65   # unapplied iogpu.wired_limit_mb reads 0; macOS's
                            # own cap is ~21GB on 32GB (gpu-wired-limit.sh)
BUCKET_MIN_GB=32            # policy, not measurement: the bucket boundaries
BUCKET_MID_GB=36            # from the header. 32GB is the minimum this stack
                            # was built (and crashed) on — below it the coder
                            # itself does not fit; 36GB is the next MacBook
                            # memory step up.

die() { printf 'error: %s\n' "$1" >&2; exit 2; }
command -v yq >/dev/null 2>&1 || die "yq not found — install with: brew install yq"
[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"

MODE="explain"
case "${1:-}" in
    --check)   MODE="check" ;;
    -h|--help) sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "")        ;;
    *)         die "unknown option: $1" ;;
esac

ram_bytes="${LOCALAI_MEMSIZE_BYTES:-$(sysctl -n hw.memsize)}"
ram_mb=$(( ram_bytes / 1048576 ))
ram_gb=$(( (ram_mb + 512) / 1024 ))

if [ "$ram_gb" -lt "$BUCKET_MIN_GB" ]; then
    bucket="below"
    bucket_desc="<${BUCKET_MIN_GB}GB — below the minimum: the pinned coder does not fit a working margin"
elif [ "$ram_gb" -eq "$BUCKET_MIN_GB" ]; then
    bucket="min"
    bucket_desc="${BUCKET_MIN_GB}GB — the minimum: only the budget holder resides on the GPU"
elif [ "$ram_gb" -le "$BUCKET_MID_GB" ]; then
    bucket="mid"
    bucket_desc="$((BUCKET_MIN_GB + 1))-${BUCKET_MID_GB}GB — a small always-resident set, gated by arithmetic"
else
    bucket="comfortable"
    bucket_desc=">${BUCKET_MID_GB}GB — comfortable: the full co-resident stack is gated"
fi

wall_src="live sysctl"
if [ -n "${LOCALAI_WIRED_MB:-}" ]; then
    wall_mb="$LOCALAI_WIRED_MB"; wall_src="override"
elif [ -n "${LOCALAI_MEMSIZE_BYTES:-}" ]; then
    wall_mb="$("$ROOT/scripts/gpu-wired-limit.sh" --target)"; wall_src="computed target"
else
    wall_mb="$(sysctl -n iogpu.wired_limit_mb 2>/dev/null || printf '0')"
    if [ "$wall_mb" -le 0 ] 2>/dev/null; then
        wall_mb=$(( ram_mb * MACOS_DEFAULT_WALL_PCT / 100 ))
        wall_src="macOS default (gpu-wired-limit.sh not applied)"
    fi
fi

dev_mb=$(( ram_mb * DEVICE_MEM_PCT / 100 ))

# The budget holder: the largest effective utilization among enabled
# rapid-mlx entries (per-model override, else the manifest default).
default_util="$(yq -r '.defaults.gpu_memory_utilization // 0.90' "$MANIFEST")"
util="$(yq -r '.models[] | select(.enabled != false and .engine == "rapid-mlx")
               | .gpu_memory_utilization // ""' "$MANIFEST" 2>/dev/null \
        | awk -v d="$default_util" '
            BEGIN { m = 0 }
            { v = ($0 == "" ? d : $0) + 0; if (v > m) m = v }
            END { printf "%s", (NR == 0 ? "" : m) }')"
if [ -z "$util" ]; then
    [ "$MODE" = "explain" ] && printf 'no enabled rapid-mlx model — nothing to budget\n'
    exit 0
fi

alloc_mb="$(awk -v u="$util" -v d="$dev_mb" 'BEGIN { printf "%d", u * d }')"
margin_mb=$(( wall_mb - alloc_mb ))

# The reserve that GATES: what is always wired beside the budget holder —
# every enabled llama-server entry that never unloads (ttl 0 or the utilities
# group), plus the spike allowance. On-demand (heavy) entries are reported
# but do not gate: the operating assumption (owner call, 2026-08-06) is that
# a heavy model is not in use while the coder sits at its allocation limit.
# That is an assumption about usage, not physics — a heavy member stays
# wired for its full ttl after its last request, and loaded-but-idle counts
# against the wall exactly like busy — so explain mode prints the overlap
# arithmetic and the stricter fraction that would cover it. Sizes are the
# actual bytes in the hf cache; a model not downloaded yet counts 0 and is
# named.
repo_dir_mb() {
    # org/name[:quant] -> du -sm of the hub dir, 0 if absent
    local _dir
    _dir="$HF_HUB_ROOT/models--$(printf '%s' "$1" | sed 's/:.*$//; s|/|--|g')"
    if [ -d "$_dir" ]; then du -sm "$_dir" 2>/dev/null | awk '{print $1}'; else printf '0'; fi
}

unsized=""
heavy_repo=""
resident_ids=""
scan_cot=1; scan_heavy=1
cot_mb=0; heavy_mb=0
[ -n "${LOCALAI_COTENANT_MB:-}" ] && { cot_mb="$LOCALAI_COTENANT_MB"; scan_cot=0; }
[ -n "${LOCALAI_HEAVY_MB:-}" ]    && { heavy_mb="$LOCALAI_HEAVY_MB"; scan_heavy=0; }
# Resident ids always come from the manifest, even when sizes are overridden:
# the bare bucket gates on their existence, not their size.
while IFS='|' read -r _mid _repo _grp _ttl; do
    [ -n "$_repo" ] || continue
    if [ "$_grp" = "utilities" ] || [ "$_ttl" = "0" ]; then
        resident_ids="$resident_ids $_mid"
        if [ "$scan_cot" -eq 1 ]; then
            _sz="$(repo_dir_mb "$_repo")"
            [ "$_sz" -eq 0 ] && unsized="$unsized $_repo"
            cot_mb=$(( cot_mb + _sz ))
        fi
    elif [ "$scan_heavy" -eq 1 ]; then
        _sz="$(repo_dir_mb "$_repo")"
        [ "$_sz" -eq 0 ] && unsized="$unsized $_repo"
        if [ "$_sz" -gt "$heavy_mb" ]; then
            heavy_mb="$_sz"
            heavy_repo="$_repo"
        fi
    fi
done <<COTENANTS
$(yq -r '.models[] | select(.enabled != false and .engine == "llama-server")
         | .id + "|" + .repo + "|" + (.group // "heavy") + "|" + ((.ttl // "") | tostring)' \
      "$MANIFEST" 2>/dev/null)
COTENANTS
reserve_mb=$(( cot_mb + SPIKE_MB ))
overlap_mb=$(( reserve_mb + heavy_mb ))

# What the bucket gates on (the header has the policy).
case "$bucket" in
    below|min)   gate_mb="$SPIKE_MB";    gate_label="the spike allowance alone" ;;
    mid)         gate_mb="$reserve_mb";  gate_label="always-resident co-tenants + spike" ;;
    comfortable) gate_mb="$overlap_mb";  gate_label="co-residents (incl. the largest heavy member) + spike" ;;
esac

suggest="$(awk -v w="$wall_mb" -v r="$gate_mb" -v d="$dev_mb" \
    'BEGIN { s = (w - r) / d; printf "%.2f", (s < 0 ? 0 : s) }')"
overlap_suggest="$(awk -v w="$wall_mb" -v r="$overlap_mb" -v d="$dev_mb" \
    'BEGIN { s = (w - r) / d; printf "%.2f", (s < 0 ? 0 : s) }')"

if [ "$MODE" = "check" ]; then
    if [ "$bucket" = "below" ]; then
        printf '%sGB is below the %sGB minimum for this manifest — the pinned coder does not leave a working margin (README: Machine fit)\n' \
            "$ram_gb" "$BUCKET_MIN_GB"
        exit 1
    fi
    if [ "$bucket" = "min" ] && [ -n "$resident_ids" ]; then
        printf '%sGB-class machine: only the budget holder can stay resident — always-wired co-tenant(s):%s; give each a ttl and group: heavy, or move it off the GPU\n' \
            "$ram_gb" "$resident_ids"
        exit 1
    fi
    if [ "$margin_mb" -lt "$gate_mb" ]; then
        printf 'utilization %s leaves %sMB under the %sMB wired limit; %s need ~%sMB — a fraction <= %s fits\n' \
            "$util" "$margin_mb" "$wall_mb" "$gate_label" "$gate_mb" "$suggest"
        exit 1
    fi
    exit 0
fi

chip="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || printf 'unknown')"

printf 'RAM                    %s MB\n' "$ram_mb"
printf 'machine bucket         %s\n' "$bucket_desc"
printf 'chip                   %s   (all FINDINGS numbers are from an M5 — see README)\n' "$chip"
printf 'wired limit (wall)     %s MB   (%s)\n' "$wall_mb" "$wall_src"
printf 'Metal device memory    ~%s MB  (%s%% of RAM — see header)\n' "$dev_mb" "$DEVICE_MEM_PCT"
printf 'utilization            %s -> allocation limit ~%s MB\n' "$util" "$alloc_mb"
printf 'margin under the wall  %s MB\n' "$margin_mb"
printf 'reserve (residents)    %s MB   (always-resident co-tenants %s MB + spike allowance %s MB)\n' \
    "$reserve_mb" "$cot_mb" "$SPIKE_MB"
printf 'gate (this bucket)     %s MB   (%s)\n' "$gate_mb" "$gate_label"
[ -n "$unsized" ] && printf 'not yet downloaded     %s (counted as 0)\n' "$unsized"
if [ "$heavy_mb" -gt 0 ] && [ "$bucket" != "comfortable" ]; then
    printf 'heavy overlap          +%s MB while %s is wired -> %s MB; a fraction <= %s covers it\n' \
        "$heavy_mb" "${heavy_repo:-the largest on-demand model}" "$overlap_mb" "$overlap_suggest"
    printf '                       transient in this bucket, reported but not gating — and a heavy\n'
    printf '                       member stays wired for its full ttl after its last request (#16)\n'
fi
if [ "$bucket" = "below" ]; then
    printf 'verdict                BELOW THE %sGB MINIMUM — the pinned coder does not leave a\n' "$BUCKET_MIN_GB"
    printf '                       working margin on this machine (README: Machine fit)\n'
    exit 1
fi
if [ "$bucket" = "min" ] && [ -n "$resident_ids" ]; then
    printf 'verdict                RESIDENT CO-TENANT on a %sGB-class machine:%s\n' "$ram_gb" "$resident_ids"
    printf '                       only the budget holder can stay resident — give each a ttl and\n'
    printf '                       group: heavy, or move it off the GPU (FINDINGS #16)\n'
    exit 1
fi
if [ "$margin_mb" -lt "$gate_mb" ]; then
    printf 'verdict                SHORT by %s MB — a fraction <= %s fits (FINDINGS #16)\n' \
        "$(( gate_mb - margin_mb ))" "$suggest"
    exit 1
fi
printf 'verdict                fits, %s MB to spare\n' "$(( margin_mb - gate_mb ))"
