#!/usr/bin/env bash
# Hermetic tests for probe.sh — no network, no service, no launchctl.
# Sources probe.sh with FIELD_KIT_LIB_ONLY=1 and exercises the pure logic.
#
# shellcheck disable=SC2015  # `cond && ok || bad` is safe: ok/bad return 0
# shellcheck disable=SC2030,SC2031  # per-check subshells isolate env on purpose
# shellcheck disable=SC2034  # vars like STACK are read by the sourced functions

set -uo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="$(mktemp -d "${TMPDIR:-/tmp/}field-kit-test.XXXXXX")"
trap 'rm -rf "$W"' EXIT

PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); return 0; }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); return 0; }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

hdr "check 1 — shellcheck"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -x -e SC2329 "$KIT/probe.sh" "$KIT/tests/run.sh" \
        && ok "probe.sh and tests are shellcheck-clean" \
        || bad "shellcheck findings above"
else
    printf '  \033[2mskip — shellcheck not installed\033[0m\n'
fi

hdr "check 2 — usage and dispatch"
out="$(bash "$KIT/probe.sh" 2>&1)" && bad "no-args should exit non-zero" \
    || ok "no-args prints usage and exits non-zero"
printf '%s' "$out" | grep -q 'field-kit probe' \
    && ok "usage text is the header comment" || bad "usage text wrong"

hdr "check 3 — state, report, RESULT lines (sourced)"
(
    set -e
    export FIELD_KIT_LIB_ONLY=1
    # shellcheck disable=SC1091
    . "$KIT/probe.sh"
    KIT_ROOT="$W/kit"; RESULTS="$W/kit/probe-results"
    REPORT="$RESULTS/report.md"; STATE="$RESULTS/state"
    mkdir -p "$KIT_ROOT"

    state_set contract restore
    state_set serve_pid 12345
    state_set contract keep          # overwrite, not append
    [ "$(state_get contract)" = "keep" ] || exit 1
    [ "$(state_get serve_pid)" = "12345" ] || exit 1
    [ "$(grep -c '^contract ' "$STATE")" = "1" ] || exit 1

    result verify ok "12 passed" > "$W/result-line.txt"
    grep -q '^FIELD-KIT RESULT verify ok 12 passed$' "$W/result-line.txt" || exit 1
    grep -q '^# field-kit-report v1' "$REPORT" || exit 1
    grep -q 'RESULT verify ok' "$REPORT" || exit 1

    stage_deviation "replaced a stuck download" >/dev/null
    stage_deviation "second note" >/dev/null
    [ "$(grep -c '^## deviations' "$REPORT")" = "1" ] || exit 1
    grep -q 'replaced a stuck download' "$REPORT" || exit 1
) && ok "state round-trips, report has schema header, RESULT mirrored, deviations append once" \
  || bad "sourced-logic check failed"

hdr "check 4 — setup summary parsing"
(
    set -e
    export FIELD_KIT_LIB_ONLY=1
    # shellcheck disable=SC1091
    . "$KIT/probe.sh"
    cat > "$W/setup-out.txt" <<'EOF'
  [ok]      llama.cpp
  [install] rapid-mlx venv

== summary
  ok 12 · installed 1 · patched 0 · changed 0 · skipped 2 · manual 1
EOF
    s="$(summary_of "$W/setup-out.txt")"
    [ "$s" = "ok 12 · installed 1 · patched 0 · changed 0 · skipped 2 · manual 1" ] || exit 1
) && ok "summary_of extracts the counts line" || bad "summary parsing wrong"

hdr "check 5 — provenance snapshot against a fixture stack"
mkdir -p "$W/stack/steps" "$W/hub/models--org--present" "$W/home/.pi" "$W/bin"
cat > "$W/stack/steps/10-tools.sh" <<'EOF'
TOOLS="
zzfaketool:zzfakebin
jq:jq
"
EOF
cat > "$W/stack/models.yaml" <<'EOF'
models:
  - id: a
    repo: "org/present"
  - id: b
    repo: "org/absent2:Q8_0"
EOF
cat > "$W/bin/brew" <<'EOF'
#!/bin/bash
case "$1" in
    list) printf 'jq\n' ;;
    tap)  : ;;
esac
exit 0
EOF
chmod +x "$W/bin/brew"
(
    set -e
    export FIELD_KIT_LIB_ONLY=1
    # shellcheck disable=SC1091
    . "$KIT/probe.sh"
    STACK="$W/stack"; RESULTS="$W/prov-results"; PROV="$RESULTS/provenance.txt"
    export HOME="$W/home" HF_HUB_CACHE="$W/hub" PATH="$W/bin:/usr/bin:/bin"
    snapshot_provenance >/dev/null
    grep -q '^formula zzfaketool absent$' "$PROV" || { echo "zzfaketool"; exit 1; }
    grep -q '^formula jq present$' "$PROV" || { echo "jq"; exit 1; }
    grep -q '^tap mostlygeek/llama-swap absent$' "$PROV" || { echo "tap"; exit 1; }
    grep -q "^dir $W/home/.pi present\$" "$PROV" || { echo ".pi"; exit 1; }
    grep -q "^dir $W/home/.rapid-mlx absent\$" "$PROV" || { echo ".rapid-mlx"; exit 1; }
    grep -q '^hfrepo org/present present$' "$PROV" || { echo "hfrepo present"; exit 1; }
    grep -q '^hfrepo org/absent2 absent$' "$PROV" || { echo "hfrepo absent (quant stripped)"; exit 1; }
    # every line obeys the grammar uninstall.sh consumes
    grep -v '^#' "$PROV" | grep -vqE '^(formula|tap|dir|hfrepo) .+ (present|absent)$' \
        && { echo "grammar"; exit 1; }
    exit 0
) && ok "provenance records present/absent per kind, strips quants, keeps the grammar" \
  || bad "provenance snapshot wrong (marker above)"

hdr "check 6 — consent refuses to guess in non-interactive runs"
(
    export FIELD_KIT_LIB_ONLY=1
    # shellcheck disable=SC1091
    . "$KIT/probe.sh"
    unset FIELD_KIT_YES
    consent "anything" </dev/null >/dev/null 2>&1
) && bad "consent proceeded without a terminal or FIELD_KIT_YES" \
  || ok "consent dies without a terminal unless FIELD_KIT_YES=1"

hdr "check 7 — the bundled machine-report runs bare and matches the stack formula"
env -i HOME="$W" PATH=/usr/bin:/bin:/usr/sbin bash "$KIT/machine-report.sh" > "$W/mr.txt" 2>&1 \
    && ok "runs with a bare PATH and empty environment" \
    || bad "needs something a stock Mac does not have: $(tail -2 "$W/mr.txt")"
# Pin the wired-limit formula (min of 75% and RAM-8GB, floored to a GB) so a
# stale distribution copy fails here instead of misleading a friend. 48GB is
# the case the two rules disagree on: 36864, not 40960.
LOCALAI_MEMSIZE_BYTES=$((48 * 1073741824)) bash "$KIT/machine-report.sh" > "$W/mr48.txt" 2>&1
grep -q 'would set 36864 MB' "$W/mr48.txt" \
    && ok "48GB wired-limit target is 36864 (75% rule binds)" \
    || bad "formula drifted from the stack: $(grep 'would set' "$W/mr48.txt")"

hdr "check 8 — bandwidth table and chip-generation detection"
(
    set -e
    export FIELD_KIT_LIB_ONLY=1
    # shellcheck disable=SC1091
    . "$KIT/probe.sh"
    for pair in "Apple M1:68" "Apple M2 Pro:200" "Apple M3 Max:400" \
                "Apple M4 Pro:273" "Apple M1 Ultra:800" "Apple M5:153" \
                "Intel(R) Core(TM):unknown"; do
        _chip="${pair%:*}"; _want="${pair##*:}"
        _got="$(FIELD_KIT_CHIP="$_chip" bandwidth_estimate_gbs)"
        [ "$_got" = "$_want" ] || { echo "$_chip -> $_got (want $_want)"; exit 1; }
    done
    FIELD_KIT_CHIP="Apple M4 Pro" is_pre_m5 || { echo "M4 should be pre-M5"; exit 1; }
    if FIELD_KIT_CHIP="Apple M5" is_pre_m5; then echo "M5 is not pre-M5"; exit 1; fi
    exit 0
) && ok "estimates match public specs per tier; pre-M5 detection correct" \
  || bad "bandwidth/chip logic wrong (marker above)"

hdr "check 9 — in-flight fraction tuning edits the value, nothing else"
(
    set -e
    export FIELD_KIT_LIB_ONLY=1
    # shellcheck disable=SC1091
    . "$KIT/probe.sh"
    cat > "$W/tune-manifest.yaml" <<'EOF'
#   gpu_memory_utilization   override defaults.gpu_memory_utilization
defaults:
  ttl: 600
models:
  - id: coder
    gpu_memory_utilization: 0.85
EOF
    manifest_set_fraction "$W/tune-manifest.yaml" 0.74
    grep -q 'gpu_memory_utilization: 0.74' "$W/tune-manifest.yaml" || { echo "value not set"; exit 1; }
    grep -q '^#   gpu_memory_utilization   override' "$W/tune-manifest.yaml" || { echo "comment mangled"; exit 1; }
    [ "$(grep -c '0.74' "$W/tune-manifest.yaml")" = "1" ] || { echo "wrong line count"; exit 1; }
    ( manifest_set_fraction "$W/tune-manifest.yaml" 74 2>/dev/null ) && { echo "bad input accepted"; exit 1; }
    # the tune label reaches the per-measurement conditions line
    RESULTS="$W/tune-results"; STATE="$RESULTS/state"
    state_set tune_label tune2
    conditions_line | grep -q 'tune=tune2' || { echo "conditions missing tune"; exit 1; }
    exit 0
) && ok "fraction edit is surgical, bad input refused, tune label stamps conditions" \
  || bad "tune logic wrong (marker above)"

hdr "check 10 — decode spread survives the average"
(
    set -e
    export FIELD_KIT_LIB_ONLY=1
    # shellcheck disable=SC1091
    . "$KIT/probe.sh"
    # 3 clean reps (declining — the FINDINGS #17 shape), one ERROR row that
    # must be excluded, one non-decode row that must not match.
    printf 'installed\tdecode-1\t1000\t13\t50\ninstalled\tdecode-2\t1000\t10\t50\ninstalled\tdecode-3\t1000\t4\t50\ninstalled\tdecode-4\tERROR\t(engine died)\ninstalled\tprefill-cold\t2000\t8\t14200\n' \
        > "$W/ab-fixture.tsv"
    s="$(decode_stats "$W/ab-fixture.tsv")"
    [ "$s" = "9.0 4.0 13.0" ] || { echo "stats: $s (want 9.0 4.0 13.0)"; exit 1; }
    printf 'installed\tdecode-1\tERROR\t(nothing clean)\n' > "$W/ab-empty.tsv"
    s="$(decode_stats "$W/ab-empty.tsv")"
    [ "$s" = "? ? ?" ] || { echo "empty stats: $s (want ? ? ?)"; exit 1; }
    exit 0
) && ok "avg/min/max from clean reps only; ERROR rows excluded; no-data prints ?" \
  || bad "decode_stats wrong (marker above)"

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
