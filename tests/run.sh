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

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
