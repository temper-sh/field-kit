# steps/20-engines.sh — inference engines, the router, and the coding agent.
#
# Sourced by setup.sh.
# shellcheck shell=bash

section "engines"

# ── brew formulae ────────────────────────────────────────────────────────────
# llama.cpp provides llama-server (GGUF), hf is the HuggingFace CLI, uv
# manages the standalone interpreter for the rapid-mlx venv below.
for _entry in "llama.cpp:llama-server" "hf:hf" "uv:uv"; do
    _formula="${_entry%%:*}"
    _binary="${_entry##*:}"
    if brew_has "$_formula" || have "$_binary"; then
        ok "$_formula"
    else
        do_install "$_formula" brew install "$_formula"
    fi
done

# Pi. A pnpm/npm-installed `pi` is a perfectly good install — don't shadow it
# with a second copy from brew just to satisfy a formula check.
if have pi; then
    ok "pi ($(pi --version 2>/dev/null | head -1) at $(command -v pi))"
elif brew_has pi-coding-agent; then
    ok "pi-coding-agent"
else
    do_install "pi-coding-agent" brew install pi-coding-agent
fi

# llama-swap lives in its own tap.
if brew tap 2>/dev/null | grep -qx "mostlygeek/llama-swap"; then
    ok "tap mostlygeek/llama-swap"
else
    do_install "tap mostlygeek/llama-swap" brew tap mostlygeek/llama-swap
fi

# Newer Homebrew refuses to load formulas from an untrusted third-party tap
# (first hit on a clean machine, 2026-08-07); older brews have no `trust`
# command and need nothing. Captured once, not piped — a `brew | grep -q`
# under pipefail can drop lines on SIGPIPE.
if brew trust --help >/dev/null 2>&1; then
    _trusted="$(brew trust 2>/dev/null || true)"
    case "$_trusted" in
        *mostlygeek/llama-swap*) ok "tap mostlygeek/llama-swap trusted" ;;
        *) do_install "trust tap mostlygeek/llama-swap" brew trust --tap mostlygeek/llama-swap ;;
    esac
fi

if brew_has llama-swap || have llama-swap; then
    ok "llama-swap"
else
    do_install "llama-swap" brew install llama-swap
fi

# ── Rapid-MLX ────────────────────────────────────────────────────────────────
# Served from a uv-managed venv at ~/.rapid-mlx/venv (DECISIONS' dependency
# note; the engine itself owns ~/.rapid-mlx as its state home — it drops
# session_seen there at runtime — so the venv nests inside rather than
# claiming the directory;
# re-opened 2026-08-06 after one day on the formula). The homebrew bottle
# depends on brew's mlx 0.32.0 against upstream's load-bearing `mlx<0.32`
# pin: mlx 0.32.0 runs prefill at ~1/3 baseline on the coder (FINDINGS #19's
# correction — check 12 and short probes cannot see it). pip inside the venv
# honours the pin, so the venv gets a compliant mlx. When a newer formula
# lands, adoption is deliberate and manual: verify its mlx dep satisfies the
# upstream pin, then run FINDINGS #19's gate — check 12 PLUS a context-sized
# timing probe — before switching the stack back and removing the venv.
RAPIDMLX_VENV="$HOME/.rapid-mlx/venv"
RAPIDMLX_FORMULA_BLOCKED="0.12.4"

install_rapidmlx_venv() {
    UV_PYTHON_PREFERENCE=only-managed uv python install 3.12
    UV_PYTHON_PREFERENCE=only-managed uv venv --python 3.12 "$RAPIDMLX_VENV"
    uv pip install --python "$RAPIDMLX_VENV/bin/python" "rapid-mlx==$RAPIDMLX_FORMULA_BLOCKED"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$RAPIDMLX_VENV/bin/rapid-mlx" "$HOME/.local/bin/rapid-mlx"
}

if [ -x "$RAPIDMLX_VENV/bin/rapid-mlx" ]; then
    ok "rapid-mlx ($("$RAPIDMLX_VENV/bin/rapid-mlx" --version 2>/dev/null | head -1) — uv venv; bottle $RAPIDMLX_FORMULA_BLOCKED blocked, FINDINGS #19)"
    if brew_has rapid-mlx; then
        note "brew rapid-mlx is installed and its bottle pairs mlx 0.32.0 with an engine that forbids it (FINDINGS #19):"
        note "  brew uninstall rapid-mlx"
    fi
elif is_dry; then
    # `brew info` may fetch the formula API, and the offline suite's dry-runs
    # must stay hermetic — the venv is created on real runs only.
    skip "rapid-mlx (uv venv created on a real run — bottle $RAPIDMLX_FORMULA_BLOCKED is refused, FINDINGS #19)"
else
    _rapidmlx_bottled="$(brew info --json=v2 rapid-mlx 2>/dev/null | jq -r '.formulae[0].versions.stable // empty' 2>/dev/null || true)"
    if [ -n "$_rapidmlx_bottled" ] && [ "$_rapidmlx_bottled" != "$RAPIDMLX_FORMULA_BLOCKED" ]; then
        manual "rapid-mlx — formula moved to $_rapidmlx_bottled; check its mlx dep against upstream's pin and run FINDINGS #19's gate, then:" \
            brew install rapid-mlx
    fi
    do_install "rapid-mlx uv venv (py3.12, rapid-mlx==$RAPIDMLX_FORMULA_BLOCKED, pip-compliant mlx)" install_rapidmlx_venv
fi

# ── record the engine bin dir for the launchd service ────────────────────────
# launchd runs with a bare environment. If rapid-mlx is not on the service's
# PATH, every model launch fails with "command not found" and llama-swap reports
# an opaque upstream error.
STATE_RAPIDMLX="$STATE_DIR/rapidmlx_bin"

# Decide the directory by path. The venv wins while it exists — that is the
# posture (FINDINGS #19): a brew-installed rapid-mlx must never silently
# shadow it on the service PATH. Deciding from `command -v` would flip the
# record between runs — a [change] on every run is a ground-rule-4
# violation. Both the idempotency check and the recorder go through this one
# function so they can never disagree.
rapidmlx_desired_bin() {
    local brewbin
    brewbin="$(brew --prefix 2>/dev/null || printf '/opt/homebrew')/bin"
    if [ -x "$RAPIDMLX_VENV/bin/rapid-mlx" ]; then
        printf '%s\n' "$RAPIDMLX_VENV/bin"
    elif [ -x "$brewbin/rapid-mlx" ]; then
        printf '%s\n' "$brewbin"
    elif have rapid-mlx; then
        dirname "$(command -v rapid-mlx)"
    else
        printf '%s\n' "$HOME/.local/bin"
    fi
}

record_rapidmlx_bin() {
    rapidmlx_desired_bin >"$STATE_RAPIDMLX"
}

if is_dry && ! have rapid-mlx && ! brew_has rapid-mlx; then
    skip "record rapid-mlx bin dir (rapid-mlx not installed yet)"
elif [ "$(cat "$STATE_RAPIDMLX" 2>/dev/null)" = "$(rapidmlx_desired_bin)" ]; then
    ok "rapid-mlx bin dir recorded ($(cat "$STATE_RAPIDMLX"))"
else
    do_install "record rapid-mlx bin dir -> .state/rapidmlx_bin" record_rapidmlx_bin
fi

# ── Pi permission-gate extension ─────────────────────────────────────────────
# Ships as an example in Pi's own package. Idempotency check is "file exists" —
# it is a starting point the user is expected to edit, so never overwrite it.
PI_EXT_DIR="$HOME/.pi/agent/extensions"
PERMISSION_GATE="$PI_EXT_DIR/permission-gate.ts"
PERMISSION_GATE_URL="https://raw.githubusercontent.com/earendil-works/pi/main/examples/extensions/permission-gate.ts"

# find_pi_example <filename> — locate an example shipped with the installed Pi.
find_pi_example() {
    local name="$1" root found
    for root in \
        "$(brew --prefix 2>/dev/null)/Cellar/pi-coding-agent" \
        "$HOME/.pnpm" \
        "$HOME/.npm-global/lib/node_modules" \
        "/opt/homebrew/lib/node_modules" \
        "/usr/local/lib/node_modules"
    do
        [ -d "$root" ] || continue
        found="$(find "$root" -path "*pi-coding-agent/examples/extensions/$name" \
                 -type f -print 2>/dev/null | head -1)"
        [ -n "$found" ] && { printf '%s\n' "$found"; return 0; }
    done
    return 1
}

install_permission_gate() {
    mkdir -p "$PI_EXT_DIR"
    local src
    if src="$(find_pi_example permission-gate.ts)"; then
        cp "$src" "$PERMISSION_GATE"
        note "copied from $src"
    else
        curl -fsSL "$PERMISSION_GATE_URL" -o "$PERMISSION_GATE"
        note "downloaded from earendil-works/pi"
    fi
}

if [ -f "$PERMISSION_GATE" ]; then
    ok "pi permission-gate extension"
else
    do_install "pi permission-gate extension" install_permission_gate
fi
