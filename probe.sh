#!/usr/bin/env bash
#
# field-kit probe — install the local-ai-setup stack on THIS machine, verify
# it serves, soak it, measure it, and produce one paste-able report block.
#
#   ./probe.sh run                  guided: all stages, asking before each cost
#   ./probe.sh <stage>              re-enter one stage: preflight | fetch |
#                                   install | verify | fit | perf | report | cleanup
#   ./probe.sh perf --ab            add the two-engine A/B (extra ~16GB download)
#   ./probe.sh tune --fraction 0.NN in-flight tuning: set the GPU utilization
#   ./probe.sh tune <models.yaml>     fraction, or swap in a new candidate
#                                     manifest — recorded, re-rendered through
#                                     the stack's parse gate, then re-measure
#   ./probe.sh deviation "<text>"   record an intervention (agents: MANDATORY
#                                   after any fix you make — see AGENT.md)
#   ./probe.sh serve-stop           stop the foreground llama-swap, if running
#
# Environment:
#   FIELD_KIT_STACK_REPO    git URL or local path of local-ai-setup (required
#                           for fetch unless stack/ already exists)
#   FIELD_KIT_CONTRACT      keep | restore — what happens to this machine at
#                           the end; asked interactively if unset
#   FIELD_KIT_YES           1 = consent to every stage cost up front
#                           (non-interactive / agent-driven runs)
#   FIELD_KIT_PORT          llama-swap port (default 8080)
#   FIELD_KIT_SOAK_SECONDS  fit-stage soak duration (default 1200)
#   FIELD_KIT_SOAK_EXTRACT  1 = fire a small extraction after each fit turn,
#                           keeping the extractor cycling through the heavy
#                           group during the soak (co-residency variant)
#   FIELD_KIT_PROCEED_BELOW_MIN  1 = probe on despite a below-minimum bucket
#   FIELD_KIT_MANIFEST      candidate models.yaml to probe INSTEAD of the
#                           stack default — how per-bucket tuning gets tested
#   FIELD_KIT_TURN_TIMEOUT  fit-stage per-turn wall budget in seconds
#                           (default derives from the measured prefill speed)
#
# Ground rules, inherited from the stack repo: never sudo (commands are
# printed for the human), never launchctl (llama-swap runs in the foreground;
# a launchd job appears only if the friend KEEPS the install, via the stack's
# own setup), nothing phones home — the report is a file you paste back.

set -euo pipefail

KIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK="$KIT_ROOT/stack"
RESULTS="$KIT_ROOT/probe-results"
REPORT="$RESULTS/report.md"
PROV="$RESULTS/provenance.txt"
STATE="$RESULTS/state"
SWAP_LOG="$RESULTS/llama-swap.log"

PORT="${FIELD_KIT_PORT:-8080}"
BASE="http://127.0.0.1:$PORT"
SOAK_SECONDS="${FIELD_KIT_SOAK_SECONDS:-1200}"
SCHEMA="field-kit-report v1"

PLIST="$HOME/Library/LaunchAgents/com.llamaswap.server.plist"

# ── output ───────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_RED=""; C_YEL=""; C_DIM=""; C_OFF=""
fi
say()  { printf '%s\n' "$1"; }
note() { printf '  %s%s%s\n' "$C_DIM" "$1" "$C_OFF"; }
warn() { printf '  %swarn%s  %s\n' "$C_YEL" "$C_OFF" "$1"; }
die()  { printf '%sfail%s  %s\n' "$C_RED" "$C_OFF" "$1" >&2; exit 1; }

# The machine-parseable outcome line. One per stage, always on stdout, always
# mirrored into the report. Agents key off these; keep the shape stable.
result() { # $1 = stage, $2 = ok|fail, $3 = detail
    printf 'FIELD-KIT RESULT %s %s %s\n' "$1" "$2" "$3"
    report_line ""
    report_line "**RESULT $1 $2** — $3"
}

# ── state + report ───────────────────────────────────────────────────────────
state_set() { # $1 = key, $2 = value
    mkdir -p "$RESULTS"
    touch "$STATE"
    grep -v "^$1 " "$STATE" > "$STATE.tmp" 2>/dev/null || true
    printf '%s %s\n' "$1" "$2" >> "$STATE.tmp"
    mv "$STATE.tmp" "$STATE"
}
state_get() { # $1 = key
    [ -f "$STATE" ] || return 1
    grep "^$1 " "$STATE" | head -1 | cut -d' ' -f2- || return 1
}

report_init() {
    mkdir -p "$RESULTS"
    [ -f "$REPORT" ] && return 0
    {
        printf '# %s\n\n' "$SCHEMA"
        printf 'started      %s\n' "$(date '+%Y-%m-%d %H:%M %Z')"
        printf 'kit rev      %s\n' "$(git -C "$KIT_ROOT" rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
    } > "$REPORT"
}
report_line() { report_init; printf '%s\n' "$1" >> "$REPORT"; }
report_section() { report_line ""; report_line "## $1"; report_line ""; }
report_file() { # $1 = title, $2 = file to embed verbatim
    report_line ""
    report_line '```'
    report_line "# $1"
    cat "$2" >> "$REPORT"
    report_line '```'
}

# ── consent ──────────────────────────────────────────────────────────────────
consent() { # $1 = what is about to happen (cost stated plainly)
    say ""
    say "  next: $1"
    if [ "${FIELD_KIT_YES:-0}" = "1" ]; then
        note "proceeding (FIELD_KIT_YES=1)"
        return 0
    fi
    if [ ! -t 0 ]; then
        die "not a terminal and FIELD_KIT_YES is unset — an agent must get the human's consent and pass FIELD_KIT_YES=1 (see AGENT.md)"
    fi
    printf '  proceed? [y/N] '
    read -r _ans
    case "$_ans" in y|Y|yes) return 0 ;; *) die "stopped at consent" ;; esac
}

require_contract() {
    _c="$(state_get contract || true)"
    if [ -n "${_c:-}" ]; then return 0; fi
    _c="${FIELD_KIT_CONTRACT:-}"
    if [ -z "$_c" ] && [ -t 0 ]; then
        say ""
        say "  Before anything installs, decide how this ends:"
        say "    keep     — you keep a working local LLM stack (a launchd service, ~20GB)"
        say "    restore  — everything the probe added is removed at the end"
        printf '  keep or restore? '
        read -r _c
    fi
    case "$_c" in
        keep|restore) ;;
        *) die "set FIELD_KIT_CONTRACT=keep or restore (the friend's choice, stated before stage 1)" ;;
    esac
    state_set contract "$_c"
    report_section "contract"
    report_line "keep-or-restore: **$_c**"
}

# ── measurements everyone shares ─────────────────────────────────────────────
swap_used_mb() {
    # "total = 2048.00M  used = 1024.75M  free = ..." — normalize to MB.
    sysctl -n vm.swapusage 2>/dev/null | awk '{
        v = $6 + 0
        if ($6 ~ /G/) v = v * 1024
        printf "%d", v
    }'
}
wall_now() {
    _w="$(sysctl -n iogpu.wired_limit_mb 2>/dev/null || printf 0)"
    if [ "${_w:-0}" -gt 0 ] 2>/dev/null; then printf '%s MB (raised)' "$_w"
    else printf 'macOS default'
    fi
}
# Cheap no-sudo system stats. Real chip °C needs sudo powermetrics or a
# third-party SMC tool, so the honest signal is whether macOS recorded a
# throttle (pmset only lists CPU_Speed_Limit once it has) plus the power
# source — a fanless Air on battery throttles first, and either one
# explains a decode decline that would otherwise read as FINDINGS #17.
therm_now() {
    _t="$(pmset -g therm 2>/dev/null | awk '/CPU_Speed_Limit/ {print $NF}')"
    if [ -n "${_t:-}" ] && [ "${_t:-100}" -lt 100 ] 2>/dev/null; then
        printf 'cpulimit%s%%' "$_t"
    else
        printf 'ok'
    fi
}
power_now() {
    case "$(pmset -g batt 2>/dev/null | head -1)" in
        *'AC Power'*) printf 'ac' ;;
        *'Battery Power'*) printf 'batt' ;;
        *) printf '?' ;;
    esac
}
load_now() { sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' || printf '?'; }

conditions_line() { # every measurement records what it ran under
    printf 'wall=%s swap=%sMB tune=%s therm=%s power=%s load=%s' \
        "$(wall_now)" "$(swap_used_mb)" \
        "$(state_get tune_label 2>/dev/null || printf 'default')" \
        "$(therm_now)" "$(power_now)" "$(load_now)"
}

manifest_coder() {
    yq -r '.models[] | select(.engine == "rapid-mlx") | .id' "$STACK/models.yaml" 2>/dev/null | head -1
}

manifest_extractor() {
    yq -r '.models[].id' "$STACK/models.yaml" 2>/dev/null | grep '^extract-' | head -1
}

# stdin: manifest `repo:` lines → clean org/name per line. Strips trailing
# comments, quotes, and :QUANT suffixes — every consumer must use this one
# filter (the comment case bit twice before it was shared).
repo_names_of() {
    sed 's/[[:space:]]*#.*$//; s/.*repo:[[:space:]]*"\{0,1\}//; s/"\{0,1\}[[:space:]]*$//; s/:[^/]*$//'
}

chip_brand() { printf '%s' "${FIELD_KIT_CHIP:-$(sysctl -n machdep.cpu.brand_string 2>/dev/null || printf 'unknown')}"; }
is_pre_m5() { case "$(chip_brand)" in *M5*) return 1 ;; *) return 0 ;; esac; }

# Public-spec estimate by chip identity — the perf stage MEASURES the real
# thing (decode on a memory-bound model ≈ bandwidth / weight bytes); this is
# only the preflight expectation. "unknown" is fine: the measurement stands
# on its own.
bandwidth_estimate_gbs() {
    _b="$(chip_brand)"
    _tier=base
    case "$_b" in *Ultra*) _tier=ultra ;; *Max*) _tier=max ;; *Pro*) _tier=pro ;; esac
    case "$_b" in
        *M1*) case "$_tier" in base) printf 68 ;; pro) printf 200 ;; max) printf 400 ;; ultra) printf 800 ;; esac ;;
        *M2*) case "$_tier" in base) printf 100 ;; pro) printf 200 ;; max) printf 400 ;; ultra) printf 800 ;; esac ;;
        *M3*) case "$_tier" in base) printf 100 ;; pro) printf 150 ;; max) printf 400 ;; ultra) printf 800 ;; esac ;;
        *M4*) case "$_tier" in base) printf 120 ;; pro) printf 273 ;; max) printf 546 ;; ultra) printf 546 ;; esac ;;
        *M5*) case "$_tier" in base) printf 153 ;; *) printf 'unknown' ;; esac ;;
        *) printf 'unknown' ;;
    esac
}

# Per-rep decode stats from an engine-ab TSV: prints "avg min max" tok/s, or
# "? ? ?" when no clean decode rows. The spread is what the average hides —
# a wide min–max on a foreign machine is the idle-decay signature (stack
# FINDINGS #17: decode declining rep over rep while the engine reports ready).
decode_stats() {
    awk -F'\t' '$2 ~ /^decode/ && $3 != "ERROR" && $3 + 0 > 0 {
        t = $4 * 1000 / $3
        if (n == 0 || t < min) min = t
        if (n == 0 || t > max) max = t
        ms += $3; tok += $4; n++
    } END {
        if (n && ms) printf "%.1f %.1f %.1f", tok * 1000 / ms, min, max
        else print "? ? ?"
    }' "$1"
}

# Pass an SSE stream through unchanged while stamping wall-clock seconds at
# the first and last data chunks into a sidecar file ("t0 t1"). That window
# is the turn's decode time — prefill ends when the first chunk lands. The
# bare-srand() call is awk's only portable clock; anything else forks a
# process per chunk.
sse_tee_timing() { # $1 = sidecar path; stdin -> stdout
    # Only chunks with a non-empty content delta count: rapid-mlx sends an
    # immediate first chunk before prefill starts, and the trailing usage
    # chunk has no content — stamping either would fold prefill or trailer
    # time into the decode window.
    awk -v side="$1" '
        /^data: / && /"content":"[^"]/ { if (!t0) { srand(); t0 = srand() } srand(); t1 = srand() }
        { print }
        END { if (t0) printf "%d %d\n", t0, t1 > side }'
}

# ── fetch ────────────────────────────────────────────────────────────────────
stage_fetch() {
    report_init
    if [ -d "$STACK/.git" ]; then
        note "stack already fetched: $STACK"
    else
        _src="${FIELD_KIT_STACK_REPO:-}"
        [ -n "$_src" ] || die "set FIELD_KIT_STACK_REPO to the local-ai-setup git URL or a local path (a copy on a drive works)"
        git clone "$_src" "$STACK"
    fi
    _sha="$(git -C "$STACK" rev-parse --short HEAD)"
    state_set stack_sha "$_sha"
    report_section "stack"
    report_line "source: ${FIELD_KIT_STACK_REPO:-already present}"
    report_line "stack rev: $_sha"
    result fetch ok "stack at $_sha"
}

# ── preflight ────────────────────────────────────────────────────────────────
stage_preflight() {
    # The kit ships its own machine-report.sh (distribution copy — canonical
    # in the stack repo), so preflight needs no stack clone: a below-minimum
    # machine stops before anything is fetched.
    report_section "machine"
    bash "$KIT_ROOT/machine-report.sh" > "$RESULTS/machine-report.txt"
    cat "$RESULTS/machine-report.txt"
    report_file "machine-report.sh" "$RESULTS/machine-report.txt"
    _bw="$(bandwidth_estimate_gbs)"
    report_line ""
    report_line "bandwidth estimate: ~${_bw} GB/s (chip identity, public specs — the perf stage measures the real thing)"
    note "bandwidth estimate ~${_bw} GB/s"
    if is_pre_m5; then
        note "pre-M5 chip: the two-engine A/B (perf --ab) is where this machine's data matters most"
    fi

    _fail=""
    # A thrashing box pollutes every number after this point (FINDINGS #19).
    _swap="$(swap_used_mb)"
    if [ "${_swap:-0}" -gt 2048 ]; then
        warn "swap at ${_swap}MB — reboot or quiet the machine before timing anything"
        _fail="swap"
    else
        note "swap ${_swap}MB"
    fi
    # ~20GB weights + headroom.
    _free_gb="$(df -g "$HOME" | awk 'NR==2 {print $4}')"
    if [ "${_free_gb:-0}" -lt 25 ]; then
        warn "only ${_free_gb}GB free — the stack wants ~20GB of weights plus headroom"
        _fail="${_fail:+$_fail,}disk"
    else
        note "disk ${_free_gb}GB free"
    fi
    if curl -s -o /dev/null -m 2 "$BASE/v1/models" 2>/dev/null; then
        warn "something already answers on :$PORT — set FIELD_KIT_PORT or stop it"
        _fail="${_fail:+$_fail,}port"
    else
        note "port $PORT free"
    fi
    command -v brew >/dev/null 2>&1 || {
        warn "Homebrew is not installed — https://brew.sh first, then re-run"
        _fail="${_fail:+$_fail,}brew"
    }
    xcode-select -p >/dev/null 2>&1 || {
        warn "Xcode Command Line Tools missing — xcode-select --install"
        _fail="${_fail:+$_fail,}clt"
    }
    if grep -q 'below the' "$RESULTS/machine-report.txt" &&
       [ "${FIELD_KIT_PROCEED_BELOW_MIN:-0}" != "1" ]; then
        warn "bucket verdict is below-minimum — the probe stops here (FIELD_KIT_PROCEED_BELOW_MIN=1 overrides, knowingly)"
        _fail="${_fail:+$_fail,}bucket"
    fi
    report_line ""
    report_line "preflight conditions: $(conditions_line), disk ${_free_gb:-?}GB free"
    if [ -n "$_fail" ]; then
        result preflight fail "$_fail"
        exit 1
    fi
    result preflight ok "swap=${_swap}MB disk=${_free_gb}GB port=$PORT"
}

# ── install (stage 1) ────────────────────────────────────────────────────────
# Provenance first: what existed before the probe touched anything. The
# uninstaller consumes this file; its format is documented in
# stack/scripts/uninstall.sh. Runs BEFORE the first setup.sh, so no repo
# tool (yq…) can be assumed — plain grep/sed only.
snapshot_provenance() {
    [ -f "$PROV" ] && { note "provenance already recorded"; return 0; }
    mkdir -p "$RESULTS"
    {
        printf '# field-kit provenance v1 — pre-probe state, %s\n' "$(date '+%Y-%m-%d %H:%M')"
        # Capture brew's lists once: `brew | grep -q` under pipefail is a
        # SIGPIPE race that silently mis-reads long formula lists (the same
        # bug fixed in the stack's uninstall.sh — keep both fixed).
        _brew_formulas="$(brew list --formula 2>/dev/null || true)"
        _brew_taps="$(brew tap 2>/dev/null || true)"
        # tools: parsed out of the stack's own list so the two never drift
        sed -n '/^TOOLS="/,/^"/p' "$STACK/steps/10-tools.sh" \
          | grep -E '^[a-z0-9.-]+:' | while IFS=: read -r _f _b; do
            if printf '%s\n' "$_brew_formulas" | grep -Fqx "$_f" || command -v "$_b" >/dev/null 2>&1; then
                printf 'formula %s present\n' "$_f"
            else
                printf 'formula %s absent\n' "$_f"
            fi
        done
        for _e in "llama.cpp:llama-server" "hf:hf" "uv:uv" "pi-coding-agent:pi" "llama-swap:llama-swap"; do
            _f="${_e%%:*}"; _b="${_e##*:}"
            if printf '%s\n' "$_brew_formulas" | grep -Fqx "$_f" || command -v "$_b" >/dev/null 2>&1; then
                printf 'formula %s present\n' "$_f"
            else
                printf 'formula %s absent\n' "$_f"
            fi
        done
        if printf '%s\n' "$_brew_taps" | grep -Fqx "mostlygeek/llama-swap"; then
            printf 'tap mostlygeek/llama-swap present\n'
        else
            printf 'tap mostlygeek/llama-swap absent\n'
        fi
        for _d in "$HOME/.rapid-mlx" "$HOME/.config/llama-swap" "$HOME/.pi"; do
            if [ -e "$_d" ]; then printf 'dir %s present\n' "$_d"; else printf 'dir %s absent\n' "$_d"; fi
        done
        # weights: every repo the manifest names
        _hub="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
        grep -E '^[[:space:]]+repo:' "$STACK/models.yaml" \
          | repo_names_of \
          | sort -u | while IFS= read -r _r; do
            _d="$_hub/models--$(printf '%s' "$_r" | sed 's/\//--/')"
            if [ -d "$_d" ]; then printf 'hfrepo %s present\n' "$_r"; else printf 'hfrepo %s absent\n' "$_r"; fi
        done
    } > "$PROV"
    note "provenance: $(grep -c ' absent$' "$PROV") absent, $(grep -c ' present$' "$PROV") pre-existing"
}

summary_of() { # $1 = captured setup output → the counts line
    grep -A1 '^== summary' "$1" | tail -1 | sed 's/^ *//'
}

stage_install() {
    [ -f "$STACK/setup.sh" ] || die "no stack/ — run: ./probe.sh fetch"
    require_contract
    # Per-bucket tuning gets tested by probing a CANDIDATE manifest instead
    # of the stack default. The stack clone is disposable, so overwriting
    # its models.yaml is the supported move — and the report says which
    # manifest every number belongs to.
    if [ -n "${FIELD_KIT_MANIFEST:-}" ]; then
        [ -f "$FIELD_KIT_MANIFEST" ] || die "FIELD_KIT_MANIFEST not found: $FIELD_KIT_MANIFEST"
        cp "$FIELD_KIT_MANIFEST" "$STACK/models.yaml"
        report_section "candidate manifest"
        report_line "$(basename "$FIELD_KIT_MANIFEST") (sha256 $(shasum -a 256 "$FIELD_KIT_MANIFEST" | cut -c1-12)) replaced the stack default"
        state_set tune_label candidate
    else
        state_set tune_label default
    fi
    _hub="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
    _coder_repo="$(grep -E '^[[:space:]]+repo:' "$STACK/models.yaml" | head -1 | repo_names_of)"
    _seeded=no
    [ -d "$_hub/models--$(printf '%s' "$_coder_repo" | sed 's/\//--/')" ] && _seeded=yes
    if [ "$_seeded" = "yes" ]; then
        consent "install the stack — coder weights are already seeded, downloads are small"
    else
        consent "install the stack — Homebrew packages plus a ~15GB coder download (seed the HF cache from a drive to skip it; see README)"
    fi

    snapshot_provenance
    report_section "install trial"
    report_line "coder weights pre-seeded: $_seeded"

    say "  setup.sh --dry-run…"
    _t0=$(date +%s)
    ( cd "$STACK" && ./setup.sh --dry-run ) > "$RESULTS/setup-dry.txt" 2>&1 \
        || { report_file "setup.sh --dry-run (FAILED)" "$RESULTS/setup-dry.txt"; result install fail "dry-run exited non-zero"; exit 1; }
    report_line "dry-run ($(($(date +%s) - _t0))s): $(summary_of "$RESULTS/setup-dry.txt")"

    say "  setup.sh (real run — downloads happen here)…"
    _t0=$(date +%s)
    ( cd "$STACK" && LOCALAI_SKIP_LAUNCHCTL=1 ./setup.sh ) > "$RESULTS/setup-real.txt" 2>&1 \
        || { report_file "setup.sh (FAILED)" "$RESULTS/setup-real.txt"; result install fail "real run exited non-zero"; exit 1; }
    _real_s=$(($(date +%s) - _t0))
    report_line "real run (${_real_s}s): $(summary_of "$RESULTS/setup-real.txt")"

    say "  setup.sh (second run — must change nothing)…"
    _t0=$(date +%s)
    ( cd "$STACK" && LOCALAI_SKIP_LAUNCHCTL=1 ./setup.sh ) > "$RESULTS/setup-second.txt" 2>&1 \
        || { report_file "setup.sh second run (FAILED)" "$RESULTS/setup-second.txt"; result install fail "second run exited non-zero"; exit 1; }
    _second="$(summary_of "$RESULTS/setup-second.txt")"
    report_line "second run ($(($(date +%s) - _t0))s): $_second"

    grep '^  \[manual\]' "$RESULTS/setup-real.txt" > "$RESULTS/setup-manual.txt" || true
    if [ -s "$RESULTS/setup-manual.txt" ]; then
        report_file "[manual] items (sudo — the human decides)" "$RESULTS/setup-manual.txt"
    fi

    case "$_second" in
        *"installed 0"*"patched 0"*"changed 0"*)
            result install ok "second run clean; real run ${_real_s}s; seeded=$_seeded" ;;
        *)
            result install fail "second run not clean: $_second" ;;
    esac
}

# ── foreground serve ─────────────────────────────────────────────────────────
plist_env() { /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:$1" "$PLIST" 2>/dev/null; }

serve_pid_alive() {
    _pid="$(state_get serve_pid || true)"
    [ -n "${_pid:-}" ] && kill -0 "$_pid" 2>/dev/null
}

serve_stop() {
    _pid="$(state_get serve_pid || true)"
    if [ -n "${_pid:-}" ] && kill -0 "$_pid" 2>/dev/null; then
        kill "$_pid" 2>/dev/null || true
        wait "$_pid" 2>/dev/null || true
        note "stopped foreground llama-swap (pid $_pid)"
    fi
    state_set serve_pid ""
}

ensure_serving() {
    if serve_pid_alive && curl -s -o /dev/null -m 2 "$BASE/v1/models"; then
        return 0
    fi
    if curl -s -o /dev/null -m 2 "$BASE/v1/models"; then
        die "something answers on :$PORT that this probe did not start — not measuring someone else's service"
    fi
    [ -f "$PLIST" ] || die "no rendered plist at $PLIST — run: ./probe.sh install"
    _bin="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$PLIST")"
    _cfg="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:2' "$PLIST")"
    say "  starting llama-swap in the foreground (no launchd job)…"
    # The plist carries the env launchd would have provided; the foreground
    # process gets exactly the same one, so the probe serves what the real
    # service would serve.
    env PATH="$(plist_env PATH)" \
        HF_HUB_CACHE="$(plist_env HF_HUB_CACHE)" \
        RAPID_MLX_KV_CHECKPOINT_MAX_BYTES="$(plist_env RAPID_MLX_KV_CHECKPOINT_MAX_BYTES)" \
        "$_bin" --config "$_cfg" --listen "127.0.0.1:$PORT" >> "$SWAP_LOG" 2>&1 &
    _pid=$!
    state_set serve_pid "$_pid"

    _t0=$(date +%s)
    until curl -s -o /dev/null -m 2 "$BASE/v1/models"; do
        kill -0 "$_pid" 2>/dev/null || die "llama-swap exited — tail $SWAP_LOG"
        [ $(($(date +%s) - _t0)) -gt 120 ] && die "llama-swap not answering after 120s — tail $SWAP_LOG"
        sleep 2
    done
    # Preload: the coder becomes resident with zero client requests. This is
    # the field measurement perf.sh check 12p makes on the launchd service.
    _coder="$(manifest_coder)"
    say "  waiting for the coder to preload (first load reads ~15GB)…"
    # ready state required: the name alone appears in /running while the
    # model is still starting, which read a cold 15GB load as "2s".
    until curl -s -m 5 "$BASE/running" \
        | jq -e --arg m "$_coder" 'any(.running[]?; .model == $m and .state == "ready")' >/dev/null 2>&1; do
        kill -0 "$_pid" 2>/dev/null || die "llama-swap exited during preload — tail $SWAP_LOG"
        [ $(($(date +%s) - _t0)) -gt 900 ] && die "coder not resident after 900s — tail $SWAP_LOG"
        sleep 3
    done
    _elapsed=$(($(date +%s) - _t0))
    state_set preload_s "$_elapsed"
    note "coder resident after ${_elapsed}s"
    report_line ""
    report_line "foreground serve: coder resident **${_elapsed}s** after start ($(conditions_line))"
}

# ── verify (stage 2) ─────────────────────────────────────────────────────────
ctx_probe() { # one context-sized timed request; prints "wall_s prompt_tokens"
    _body="$(jq -n --arg m "$1" --arg p "$2" \
        '{model:$m, temperature:0, max_tokens:32, messages:[{role:"user",content:$p}]}')"
    _t="$(curl -s -m 900 -w '%{time_total}' -o "$RESULTS/ctx-probe-resp.json" \
        "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d "$_body")"
    _pt="$(jq -r '.usage.prompt_tokens // 0' "$RESULTS/ctx-probe-resp.json" 2>/dev/null)"
    printf '%s %s' "$_t" "$_pt"
}

stage_verify() {
    consent "serve trial — runtime suite (first run pulls ~4GB of specialist weights) plus a context-sized timing probe; roughly 10–20 minutes"
    ensure_serving
    report_section "serve trial"
    report_line "conditions: $(conditions_line)"

    say "  runtime suite…"
    _t0=$(date +%s)
    if LOCAL_AI_BASE_URL="$BASE" bash "$STACK/tests/runtime.sh" > "$RESULTS/runtime.txt" 2>&1; then
        _rt=ok
    else
        _rt=fail
    fi
    _rt_s=$(($(date +%s) - _t0))
    _counts="$(tail -1 "$RESULTS/runtime.txt" | sed 's/\x1b\[[0-9;]*m//g')"
    report_line "runtime suite (${_rt_s}s, first-run pulls included): $_counts"
    report_file "runtime.sh" "$RESULTS/runtime.txt"

    # FINDINGS #19: a short-prompt gate passes a 3×-slower engine; a ~3.6k-token
    # full prefill is the smallest request that catches it. Run twice — the
    # second may hit the radix cache, and the gap between the two is itself
    # informative.
    say "  context-sized timing probe (~3.6k tokens, twice)…"
    _coder="$(manifest_coder)"
    _prompt="$(awk 'BEGIN{for(i=0;i<106;i++) printf "The barometric sensor array logs a calibrated pressure reading every thirty seconds and flags any drift beyond two millibars for manual review by the site engineer. "; printf "Reply with only the word ACK."}')"
    read -r _w1 _p1 <<EOF
$(ctx_probe "$_coder" "run-$$-$(date +%s) $_prompt")
EOF
    read -r _w2 _p2 <<EOF
$(ctx_probe "$_coder" "run-$$-$(date +%s)-b $_prompt")
EOF
    _tps="$(awk -v t="$_w1" -v p="$_p1" 'BEGIN{ if (t+0 > 0) printf "%.0f", p/t; else print "?" }')"
    state_set ctx_prefill_tps "$_tps"
    report_line ""
    report_line "context probe: ${_p1} tokens prefilled in ${_w1}s (~${_tps} tok/s); second run ${_w2}s ($(conditions_line))"
    note "context probe: ${_p1} tok in ${_w1}s (~${_tps} tok/s), second ${_w2}s"

    if [ "$_rt" = ok ]; then
        result verify ok "runtime suite green in ${_rt_s}s; prefill ~${_tps} tok/s at ${_p1} tok"
    else
        result verify fail "runtime suite: $_counts — the transcript is in the report"
    fi
}

# ── fit (stage 3) ────────────────────────────────────────────────────────────
# The FINDINGS #16 shape: a growing long-context streaming turn with a rerank
# fired mid-stream. An engine abort here IS the datapoint — the stage records
# it and keeps going. gpu-budget's verdict beforehand is the prediction the
# soak then tests.
stage_fit() {
    consent "fit soak — ~$((SOAK_SECONDS / 60)) minutes of long-context turns with mid-turn reranks, watching for engine aborts"
    ensure_serving
    report_section "fit trial"
    ( cd "$STACK" && scripts/gpu-budget.sh ) > "$RESULTS/gpu-budget.txt" 2>&1 || true
    report_file "gpu-budget.sh prediction" "$RESULTS/gpu-budget.txt"
    report_line "conditions: $(conditions_line), soak ${SOAK_SECONDS}s"
    _extract=""
    if [ "${FIELD_KIT_SOAK_EXTRACT:-0}" = "1" ]; then
        _extract="$(manifest_extractor)"
        if [ -n "$_extract" ]; then
            report_line "soak shape: +one small extraction after each turn (FIELD_KIT_SOAK_EXTRACT=1, model $_extract) — the extractor stays cycling through the heavy group"
        else
            warn "FIELD_KIT_SOAK_EXTRACT=1 but the manifest has no extract- model — running the default shape"
            report_line "FIELD_KIT_SOAK_EXTRACT=1 requested but no extract- model in the manifest — default shape"
        fi
    fi

    _coder="$(manifest_coder)"
    # Per-turn wall budget. A slow chip legitimately needs longer at 19k
    # tokens, and a timeout here must never masquerade as an abort — derive
    # the budget from the prefill speed verify measured, clamped to
    # [600, 1800]s; FIELD_KIT_TURN_TIMEOUT overrides.
    _vtps="$(state_get ctx_prefill_tps || true)"
    _turn_budget="${FIELD_KIT_TURN_TIMEOUT:-}"
    if [ -z "$_turn_budget" ]; then
        if [ -n "${_vtps:-}" ] && [ "${_vtps%.*}" -gt 0 ] 2>/dev/null; then
            _turn_budget=$(( 19000 * 3 / 2 / ${_vtps%.*} + 180 ))
            [ "$_turn_budget" -lt 600 ] && _turn_budget=600
            [ "$_turn_budget" -gt 1800 ] && _turn_budget=1800
        else
            _turn_budget=600
        fi
    fi
    report_line "per-turn budget: ${_turn_budget}s (from measured prefill ~${_vtps:-?} tok/s)"
    _ips_before="$RESULTS/ips-before.txt"
    find "$HOME/Library/Logs/DiagnosticReports" -maxdepth 1 -name 'Python-*.ips' 2>/dev/null \
        | sort > "$_ips_before" || true

    _filler="$(awk 'BEGIN{for(i=0;i<55;i++) printf "The ingestion pipeline batches incoming records, validates each against the schema registry, retries transient failures with exponential backoff, and emits a structured audit event for every terminal state transition. "}')"
    _msgs="$RESULTS/fit-msgs.json"
    printf '[]' > "$_msgs"
    : > "$RESULTS/fit-decode.tsv"
    _t0=$(date +%s)
    _turns=0; _errors=0; _resets=0; _max_pt=0; _extract_errors=0; _rerank_errors=0
    while [ $(($(date +%s) - _t0)) -lt "$SOAK_SECONDS" ]; do
        _turns=$((_turns + 1))
        jq --arg c "Section $_turns: $_filler Summarize the pipeline's failure handling in two sentences." \
            '. + [{role:"user",content:$c}]' "$_msgs" > "$_msgs.tmp" && mv "$_msgs.tmp" "$_msgs"
        _body="$(jq --arg m "$_coder" '{model:$m, stream:true, stream_options:{include_usage:true}, max_tokens:256, messages:.}' "$_msgs")"
        ( sleep 4; curl -s -m 120 -o /dev/null -w '%{http_code}' "$BASE/v1/rerank" -H 'Content-Type: application/json' -d '{
            "model": "rerank-qwen3-0.6b",
            "query": "how are transient failures retried?",
            "documents": ["backoff with jitter", "the cat sat on the mat", "audit events are structured"]}' \
            > "$RESULTS/fit-rerank.code" ) &
        _rr_pid=$!
        _out="$RESULTS/fit-turn.sse"
        : > "$RESULTS/fit-turn.time"
        if ! curl -s -N -m "$_turn_budget" "$BASE/v1/chat/completions" -H 'Content-Type: application/json' \
              -d "$_body" 2>&1 | sse_tee_timing "$RESULTS/fit-turn.time" > "$_out"; then
            _errors=$((_errors + 1))
            report_line "turn $_turns: curl failed ($(conditions_line))"
        fi
        wait "$_rr_pid" 2>/dev/null || true
        _rr_code="$(cat "$RESULTS/fit-rerank.code" 2>/dev/null)"
        if [ "${_rr_code:-000}" != "200" ]; then
            _rerank_errors=$((_rerank_errors + 1))
            [ "$_rerank_errors" -le 3 ] && report_line "turn $_turns: mid-stream rerank failed (HTTP ${_rr_code:-none})"
        fi
        _content="$(grep '^data: ' "$_out" | sed 's/^data: //' | grep -v '^\[DONE\]' \
            | jq -r '.choices[0].delta.content // empty' 2>/dev/null | tr -d '\n')"
        _pt="$(grep '^data: ' "$_out" | sed 's/^data: //' | grep -v '^\[DONE\]' \
            | jq -r '.usage.prompt_tokens // empty' 2>/dev/null | tail -1)"
        # No usage (engine without stream_options support, or a broken turn):
        # estimate from message bytes so the 19k reset still bounds the soak.
        [ -z "${_pt:-}" ] && _pt=$(( $(wc -c < "$_msgs") / 4 ))
        [ -n "${_pt:-}" ] && [ "${_pt:-0}" -gt "$_max_pt" ] 2>/dev/null && _max_pt="$_pt"
        _ct="$(grep '^data: ' "$_out" | sed 's/^data: //' | grep -v '^\[DONE\]' \
            | jq -r '.usage.completion_tokens // empty' 2>/dev/null | tail -1)"
        _dwin="$(awk 'NF >= 2 { printf "%d", ($2 - $1) * 1000 }' "$RESULTS/fit-turn.time" 2>/dev/null)"
        if [ -n "${_ct:-}" ] && [ "${_ct:-0}" -gt 0 ] 2>/dev/null && [ "${_dwin:-0}" -gt 0 ] 2>/dev/null; then
            printf 'fit\tdecode-turn%s\t%s\t%s\t%s\t%s\n' "$_turns" "$_dwin" "$_ct" "${_pt:-0}" "$(therm_now)" \
                >> "$RESULTS/fit-decode.tsv"
        else
            printf 'fit\tdecode-turn%s\tERROR\t(no clean decode window)\t%s\n' "$_turns" "$(therm_now)" \
                >> "$RESULTS/fit-decode.tsv"
        fi
        if [ -z "$_content" ]; then
            _errors=$((_errors + 1))
            report_line "turn $_turns: empty/broken stream — abort suspected (prompt ~${_pt:-?} tok)"
            printf '[]' > "$_msgs"
            _resets=$((_resets + 1))
        else
            jq --arg a "$_content" '. + [{role:"assistant",content:$a}]' "$_msgs" > "$_msgs.tmp" && mv "$_msgs.tmp" "$_msgs"
            if [ "${_pt:-0}" -gt 19000 ] 2>/dev/null; then
                printf '[]' > "$_msgs"
                _resets=$((_resets + 1))
            fi
        fi
        if [ -n "$_extract" ]; then
            _ebody="$(jq -n --arg m "$_extract" '{model:$m, temperature:0, max_tokens:128, messages:[
                {role:"user", content:"Extract vendor and total as JSON from: Invoice INV-9(2) from Anvil Co, total 41.50 EUR."}]}')"
            if ! curl -s -m 180 -o "$RESULTS/fit-extract.json" "$BASE/v1/chat/completions" \
                  -H 'Content-Type: application/json' -d "$_ebody" \
               || ! jq -e '.choices[0].message.content' "$RESULTS/fit-extract.json" >/dev/null 2>&1; then
                _extract_errors=$((_extract_errors + 1))
                report_line "turn $_turns: extract keep-alive failed ($(conditions_line))"
            fi
        fi
    done

    _soak_s=$(($(date +%s) - _t0))
    _ips_new="$(find "$HOME/Library/Logs/DiagnosticReports" -maxdepth 1 -name 'Python-*.ips' 2>/dev/null \
        | sort | comm -13 "$_ips_before" - | wc -l | tr -d ' ')"
    _recovered="$(grep -c 'recovered from upstream disconnection' "$SWAP_LOG" 2>/dev/null || true)"
    curl -s --max-time 6 "$BASE/logs/stream/upstream" 2>/dev/null | grep 'libc++abi' \
        > "$RESULTS/fit-libcabi.txt" || true
    report_line ""
    report_line "soak: ${_turns} turns in ${_soak_s}s, max prompt ${_max_pt} tok, ${_resets} context resets"
    _dstats="$(decode_stats "$RESULTS/fit-decode.tsv")"
    _davg="$(printf '%s' "$_dstats" | awk '{print $1}')"
    _dmin="$(printf '%s' "$_dstats" | awk '{print $2}')"
    _dmax="$(printf '%s' "$_dstats" | awk '{print $3}')"
    _dspread=""
    [ "$_dmin" != "?" ] && _dspread=" (turns ${_dmin}–${_dmax})"
    report_line "per-turn decode: ~${_davg} tok/s${_dspread} — first-to-last-chunk window, ±1s granularity; rows in fit-decode.tsv"
    if [ "$_dmin" != "?" ] && awk -v a="$_dmin" -v b="$_dmax" 'BEGIN{ exit !(a * 2 < b) }'; then
        report_line "**decode spread exceeds 2× across turns — check the therm column first (a throttle explains it); a decline with therm=ok is the idle-decay signature (stack FINDINGS #17)**"
    fi
    report_line "abort evidence: ${_errors} broken turns, ${_ips_new:-0} new crash reports, ${_recovered:-0} upstream-disconnect recoveries, ${_rerank_errors} failed reranks"
    [ -n "$_extract" ] && report_line "extract keep-alives: $((_turns - _extract_errors))/${_turns} clean"
    _shape="rerank-only"
    [ -n "$_extract" ] && _shape="rerank+extract"
    if [ "$_errors" -eq 0 ] && [ "${_ips_new:-0}" = "0" ] && [ "$_extract_errors" -eq 0 ]; then
        result fit ok "${_turns} turns, ${SOAK_SECONDS}s, shape=$_shape, zero aborts — prediction held"
    else
        result fit fail "${_errors} broken turns, ${_extract_errors} extract failures, ${_ips_new:-0} crash reports in ${_turns} turns, shape=$_shape — compare with the gpu-budget prediction above"
    fi
}

# ── perf (stage 4) ───────────────────────────────────────────────────────────
stage_perf() {
    _ab=0
    [ "${1:-}" = "--ab" ] && _ab=1
    if [ "$_ab" = "1" ]; then
        consent "perf trial WITH the two-engine A/B — an extra ~16GB GGUF download and roughly 1–2 hours"
    else
        consent "perf trial on the installed engine — decode, cold prefill, agent-shaped turns; roughly 20–30 minutes"
    fi
    ensure_serving
    report_section "perf trial"
    _swap="$(swap_used_mb)"
    report_line "conditions: $(conditions_line)"
    if [ "${_swap:-0}" -gt 1536 ]; then
        # FINDINGS #19's ~1GB rule: numbers taken above it are labeled, not scored.
        warn "swap ${_swap}MB is above the ~1GB measurement baseline — numbers below are LABELED unreliable"
        report_line "**swap above the ~1GB baseline — treat these numbers as polluted (FINDINGS #19)**"
    fi

    _coder="$(manifest_coder)"
    say "  engine-ab.sh on the installed engine…"
    AB_OUT="$RESULTS" LOCAL_AI_AB_MODEL="$_coder" \
        bash "$STACK/tests/engine-ab.sh" "$PORT" installed 3 > "$RESULTS/engine-ab-installed.txt" 2>&1 \
        || warn "engine-ab reported errors — see the TSV"
    report_file "engine-ab (installed engine)" "$RESULTS/ab-installed.tsv"

    say "  prefix-cache check (perf.sh 13)…"
    if LOCAL_AI_PERF_NO_RESTART=1 LOCAL_AI_BASE_URL="$BASE" LOCAL_AI_CODER="$_coder" \
        bash "$STACK/tests/perf.sh" > "$RESULTS/perf13.txt" 2>&1; then
        _p13="ok"
    else
        _p13="fail"
    fi
    report_line "prefix-cache check: $_p13 ($(tail -1 "$RESULTS/perf13.txt" | sed 's/\x1b\[[0-9;]*m//g'))"

    if [ "$_ab" = "1" ]; then
        stage_perf_ab
    fi

    _dstats="$(decode_stats "$RESULTS/ab-installed.tsv")"
    _decode="$(printf '%s' "$_dstats" | awk '{print $1}')"
    _dmin="$(printf '%s' "$_dstats" | awk '{print $2}')"
    _dmax="$(printf '%s' "$_dstats" | awk '{print $3}')"
    _dspread=""
    [ "$_dmin" != "?" ] && _dspread=" (reps ${_dmin}–${_dmax})"
    _prefill="$(awk -F'\t' '$2 == "prefill-cold" && $3 != "ERROR" {printf "%.0f", $5*1000/$3}' "$RESULTS/ab-installed.tsv")"
    # Decode on a memory-bound model IS a bandwidth measurement: tok/s ×
    # weight bytes ≈ effective GB/s. This is the number that buckets the
    # machine on the bandwidth axis — the preflight table was the estimate.
    _hub="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
    _crepo="$(yq -r '.models[] | select(.engine == "rapid-mlx") | .repo' "$STACK/models.yaml" 2>/dev/null | head -1 | sed 's/:[^/]*$//')"
    _cmb="$(du -sm "$_hub/models--$(printf '%s' "$_crepo" | sed 's/\//--/')" 2>/dev/null | cut -f1)"
    _eff="$(awk -v t="$_decode" -v mb="${_cmb:-0}" 'BEGIN{ if (t+0 > 0 && mb+0 > 0) printf "%.0f", t*mb/1024; else print "?" }')"
    report_line ""
    report_line "headline: decode ~${_decode} tok/s${_dspread}, cold prefill ~${_prefill:-?} tok/s, preload $(state_get preload_s || printf '?')s"
    report_line "effective bandwidth: ~${_eff} GB/s (decode × ${_cmb:-?}MB of weights; preflight estimate was ~$(bandwidth_estimate_gbs) GB/s)"
    if is_pre_m5 && [ "$_ab" = "0" ]; then
        warn "pre-M5 chip without --ab: the engine-split question this machine could answer is still open"
        report_line "**pre-M5 chip, no --ab run — the two-engine split for this bucket remains unmeasured; consider ./probe.sh perf --ab (extra ~16GB, human consent)**"
    fi
    result perf ok "decode=${_decode} prefill=${_prefill:-?} tok/s effbw=${_eff}GB/s (ab=$_ab)"

    say ""
    say "  every number above ran on: $(wall_now)."
    if ! sysctl -n iogpu.wired_limit_mb 2>/dev/null | grep -qv '^0$'; then
        say "  to also measure with the raised GPU wired limit (the stack's recommended tweak),"
        say "  have the human run:"
        say "      sudo $STACK/scripts/gpu-wired-limit.sh"
        say "  then re-run: ./probe.sh fit && ./probe.sh perf   (both re-record conditions)"
    fi
}

stage_perf_ab() {
    # The reduced FINDINGS #13 A/B: prefill on both engines, same client path.
    # The llama.cpp arm runs DIRECTLY on a scratch port with the foreground
    # swap stopped — two 15GB engines cannot coexist on a 32GB box.
    _ab_port=10098
    _gguf_id="qwen3.6-27b-mtp-gguf"
    _repo="$(yq -r ".models[] | select(.id == \"$_gguf_id\") | .repo" "$STACK/models.example.yaml")"
    _flags="$(yq -r ".models[] | select(.id == \"$_gguf_id\") | .flags" "$STACK/models.example.yaml")"
    [ -n "$_repo" ] && [ "$_repo" != "null" ] || { warn "no $_gguf_id entry in models.example.yaml — skipping the A/B arm"; return 0; }

    say "  A/B arm: llama-server on :$_ab_port (pulls ~16GB on first run)…"
    serve_stop
    # eval, not word-splitting: the manifest's flag string carries its own
    # shell quoting (--chat-template-kwargs '{...}'), and bare $_flags hands
    # the quote characters to llama-server as JSON — it refuses to start.
    eval "llama-server --port \"$_ab_port\" -hf \"$_repo\" $_flags" > "$RESULTS/llama-arm.log" 2>&1 &
    _lpid=$!
    _t0=$(date +%s)
    until curl -s -o /dev/null -m 2 "http://127.0.0.1:$_ab_port/health"; do
        kill -0 "$_lpid" 2>/dev/null || { warn "llama-server exited — tail $RESULTS/llama-arm.log"; return 0; }
        [ $(($(date +%s) - _t0)) -gt 3600 ] && { kill "$_lpid" 2>/dev/null; warn "llama-server not healthy after 1h"; return 0; }
        sleep 5
    done
    AB_OUT="$RESULTS" bash "$STACK/tests/engine-ab.sh" "$_ab_port" llamacpp 3 \
        > "$RESULTS/engine-ab-llamacpp.txt" 2>&1 || warn "llama.cpp arm reported errors"
    kill "$_lpid" 2>/dev/null || true
    wait "$_lpid" 2>/dev/null || true
    report_file "engine-ab (llama.cpp arm)" "$RESULTS/ab-llamacpp.tsv"
    ( cd "$RESULTS" && AB_OUT="$RESULTS" bash "$STACK/tests/engine-ab-summary.sh" installed llamacpp \
        > "$RESULTS/ab-summary.txt" 2>&1 ) || true
    [ -s "$RESULTS/ab-summary.txt" ] && report_file "A/B summary" "$RESULTS/ab-summary.txt"
    ensure_serving
}

# ── tune (in-flight, recorded) ───────────────────────────────────────────────
# The sanctioned mid-probe levers — PLAN §9's *mechanical* parameters, so a
# failed fit soak can become a tuning answer in the same visit (the FINDINGS
# #16 ladder: 0.85 → ≤0.74). Tuning is never silent: each application gets a
# numbered label, lands in the report's tuning section, and every subsequent
# measurement carries it via conditions_line. Editing the clone's manifest is
# allowed precisely because it is disposable — the stack's ground rule 6
# protects the owner's hand-edited file, not this copy.
manifest_set_fraction() { # $1 = manifest, $2 = new fraction
    case "$2" in
        0.[0-9]|0.[0-9][0-9]) ;;
        *) die "fraction must look like 0.NN (got '$2')" ;;
    esac
    sed -i '' "s/^\([[:space:]]*gpu_memory_utilization:[[:space:]]*\)[0-9.][0-9.]*/\1$2/" "$1"
    grep -q "gpu_memory_utilization: $2" "$1" \
        || die "manifest has no gpu_memory_utilization line to tune"
}

stage_tune() {
    [ -f "$STACK/models.yaml" ] || die "no stack/ — run: ./probe.sh fetch"
    _n="$(state_get tune_n 2>/dev/null || printf 0)"
    _n=$((_n + 1))
    cp "$STACK/models.yaml" "$RESULTS/models.pre-tune-$_n.yaml"
    case "${1:-}" in
        --fraction)
            [ -n "${2:-}" ] || die "usage: probe.sh tune --fraction 0.NN | tune <candidate.yaml>"
            _old="$(grep -m1 '^[[:space:]]*gpu_memory_utilization:' "$STACK/models.yaml" | awk '{print $2}')"
            manifest_set_fraction "$STACK/models.yaml" "$2"
            _desc="fraction ${_old:-?} -> $2"
            ;;
        "")
            die "usage: probe.sh tune --fraction 0.NN | tune <candidate.yaml>"
            ;;
        *)
            [ -f "$1" ] || die "candidate manifest not found: $1"
            cp "$1" "$STACK/models.yaml"
            _desc="manifest $(basename "$1") (sha256 $(shasum -a 256 "$1" | cut -c1-12))"
            ;;
    esac
    say "  re-rendering configs (the stack's parse gate vets the change)…"
    if ! ( cd "$STACK" && LOCALAI_SKIP_LAUNCHCTL=1 ./setup.sh --only configs ) \
            > "$RESULTS/tune-$_n.txt" 2>&1; then
        cp "$RESULTS/models.pre-tune-$_n.yaml" "$STACK/models.yaml"
        report_line "tune #$_n REJECTED by the config gate ($_desc) — manifest restored"
        die "config re-render refused the change — tail $RESULTS/tune-$_n.txt"
    fi
    state_set tune_n "$_n"
    state_set tune_label "tune$_n"
    grep -q '^## tuning' "$REPORT" 2>/dev/null || report_section "tuning"
    report_line "- tune #$_n: $_desc"
    serve_stop
    ensure_serving
    result tune ok "#$_n $_desc — now re-run the stage that prompted it"
}

# ── report / deviation / cleanup ─────────────────────────────────────────────
stage_report() {
    [ -f "$REPORT" ] || die "no report yet — run some stages first"
    say ""
    say "──── paste everything between the lines back ────"
    cat "$REPORT"
    say "──── end of report ────"
}

stage_deviation() {
    [ -n "${1:-}" ] || die 'usage: probe.sh deviation "<what was done and why>"'
    grep -q '^## deviations' "$REPORT" 2>/dev/null || report_section "deviations"
    report_line "- $(date '+%H:%M') $1"
    say "recorded: $1"
}

stage_cleanup() {
    _c="$(state_get contract || true)"
    case "${_c:-}" in
        keep)
            consent "keep: load the real launchd service via the stack's own setup"
            serve_stop
            ( cd "$STACK" && ./setup.sh ) | tail -5
            say ""
            say "  the stack now runs as a launchd service; the install lives in:"
            say "      $STACK   (keep this clone — re-running its setup.sh is how you update)"
            [ -s "$RESULTS/setup-manual.txt" ] && {
                say "  the optional sudo tweaks it recommends:"
                sed 's/^/      /' "$RESULTS/setup-manual.txt"
            }
            result cleanup ok "kept — launchd service loaded"
            ;;
        restore)
            consent "restore: remove everything the probe added (provenance-guided)"
            serve_stop
            bash "$STACK/scripts/uninstall.sh" --provenance "$PROV"
            result cleanup ok "restored per provenance"
            say ""
            say "  last step, run by you (this script cannot delete itself):"
            say "      rm -rf $KIT_ROOT"
            ;;
        *)
            die "no contract recorded — run: ./probe.sh install (it asks keep-or-restore first)"
            ;;
    esac
}

stage_run() {
    stage_preflight
    stage_fetch
    stage_install
    stage_verify
    stage_fit
    stage_perf "${1:-}"
    stage_report
    stage_cleanup
}

# ── dispatch ─────────────────────────────────────────────────────────────────
# Sourcing with FIELD_KIT_LIB_ONLY=1 loads the functions without running
# anything — the hermetic test suite does exactly that.
if [ "${FIELD_KIT_LIB_ONLY:-0}" = "1" ]; then
    # shellcheck disable=SC2317  # the exit is reachable only when executed, not sourced
    return 0 2>/dev/null || exit 0
fi

case "${1:-}" in
    run)        shift; stage_run "$@" ;;
    fetch)      stage_fetch ;;
    preflight)  stage_preflight ;;
    install)    stage_install ;;
    verify)     stage_verify ;;
    fit)        stage_fit ;;
    perf)       shift; stage_perf "$@" ;;
    tune)       shift; stage_tune "$@" ;;
    report)     stage_report ;;
    cleanup)    stage_cleanup ;;
    deviation)  shift; stage_deviation "$@" ;;
    serve-stop) serve_stop ;;
    *) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
