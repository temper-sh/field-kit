# steps/10-tools.sh — CLI primitives that coding agents shell out to.
#
# Sourced by setup.sh; uses its helpers (ok/skip/note/emit, have/brew_has, is_dry).
# shellcheck shell=bash

section "tools"

# formula:binary — the binary is checked too, so a non-brew install still counts.
TOOLS="
ripgrep:rg
fd:fd
jq:jq
yq:yq
ast-grep:ast-grep
gh:gh
difftastic:difft
bat:bat
tokei:tokei
fzf:fzf
rtk:rtk
"

_missing=""
for _entry in $TOOLS; do
    _formula="${_entry%%:*}"
    _binary="${_entry##*:}"
    if brew_has "$_formula" || have "$_binary"; then
        ok "$_formula"
    else
        _missing="$_missing $_formula"
    fi
done

# One brew invocation for everything missing — much faster than one per formula.
#
# It goes through do_install rather than running loose for two reasons. The
# counters and the --dry-run guard come for free instead of being hand-rolled,
# and `brew install` is no longer a bare command under `set -e`: a single dead
# or renamed formula used to abort the entire run, after [install] had already
# been printed for every tool in the list. Now it reports and the run continues.
install_missing_tools() {
    # shellcheck disable=SC2086
    if brew install $_missing; then
        return 0
    fi
    fail "brew install failed —$_missing"
    note "one of these formulae is probably renamed or gone; install them"
    note "individually to find which, then re-run"
    return 0
}

if [ -n "$_missing" ]; then
    do_install "brew install$_missing" install_missing_tools
fi

# macOS ships these; verify rather than install.
for _binary in git curl; do
    if have "$_binary"; then ok "$_binary"; else fail "$_binary not found"; fi
done

# The --pi-hints flag that used to live here (a tool-usage hints block
# appended to ~/.pi/agent/APPEND_SYSTEM.md) was removed 2026-08-05: measured
# useless — the model invoked `rtk` 0 times in 4 runs with the hint in its
# prompt (DECISIONS §shell-output-compressors). The tools stay for humans.
