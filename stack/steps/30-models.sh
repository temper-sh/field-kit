# steps/30-models.sh — download weights into the HF cache and apply vendored patches.
#
# Sourced by setup.sh.
# shellcheck shell=bash

section "models"

# On a fresh machine the tools step installs yq, so a full --dry-run has no yq
# yet and must not report failure for it: `./setup.sh --dry-run` is the first
# command a cautious stranger runs, and it has to exit 0.
if ! have yq; then
    if is_dry; then
        skip "models (yq not installed yet — the tools step installs it)"
    else
        fail "yq is required to read the manifest — run the tools step first"
    fi
    return 0
fi

# Standard HuggingFace cache layout. The precedence lives in setup.sh's
# hf_hub_root() because the service step has to reach the same answer when it
# writes HF_HUB_CACHE into the plist — acceptance check 8 relies on being able
# to point this at a scratch directory.
HF_HUB_ROOT="$(hf_hub_root)"

# repo_cache_dir <org/name> -> <hub>/models--org--name
# huggingface_hub builds the directory as "models--" + repo_id with every "/"
# replaced by "--"; hyphens inside org or model names are left alone.
repo_cache_dir() {
    printf '%s/models--%s\n' "$HF_HUB_ROOT" "$(printf '%s' "$1" | sed 's|/|--|g')"
}

# snapshot_dirs <org/name> — every snapshot revision directory, one per line.
snapshot_dirs() {
    local base
    base="$(repo_cache_dir "$1")/snapshots"
    [ -d "$base" ] || return 0
    find "$base" -mindepth 1 -maxdepth 1 -type d 2>/dev/null
}

# has_weights <org/name> — true when some snapshot holds real weight files.
has_weights() {
    local d
    while IFS= read -r d; do
        [ -n "$d" ] || continue
        if find "$d" -maxdepth 1 \( -name '*.safetensors' -o -name '*.gguf' -o -name '*.npz' \) \
             -print 2>/dev/null | head -1 | grep -q .; then
            return 0
        fi
    done <<EOF
$(snapshot_dirs "$1")
EOF
    return 1
}

# ── downloads ────────────────────────────────────────────────────────────────
for _id in $(manifest_ids); do
    _engine="$(mval "$_id" '.engine' 'rapid-mlx')"
    _repo="$(mval "$_id" '.repo' '')"

    case "$_engine" in
        llama-server)
            # llama-server -hf resolves and downloads the GGUF (and its mmproj,
            # when the repo has one) on the first request that loads the model.
            skip "$_id — downloads on first request"
            ;;
        rapid-mlx)
            if [ -z "$_repo" ]; then
                fail "$_id has no repo"
            elif has_weights "$_repo"; then
                ok "$_id ($_repo cached)"
            else
                do_install "$_id — download $_repo" hf download "$_repo"
            fi
            ;;
        *)
            fail "$_id has unknown engine '$_engine'"
            ;;
    esac
done

# ── optional checksum verification ───────────────────────────────────────────
# Checksumming a 16GB snapshot is slow, hence opt-in. `hf cache prune` is never
# run automatically — it deletes data; see the README for manual maintenance.
if [ "$VERIFY" -eq 1 ]; then
    for _id in $(manifest_ids); do
        [ "$(mval "$_id" '.engine' 'rapid-mlx')" = "rapid-mlx" ] || continue
        _repo="$(mval "$_id" '.repo' '')"
        [ -n "$_repo" ] || continue
        if is_dry; then
            skip "verify $_repo (dry-run)"
        elif hf cache verify "$_repo" >/dev/null 2>&1; then
            ok "verify $_repo"
        else
            fail "verify $_repo — checksum mismatch or missing revision"
        fi
    done
else
    skip "checksum verification (pass --verify)"
fi

# ── patches ──────────────────────────────────────────────────────────────────
# A patch directory holds the files to force into every snapshot of a model,
# by basename. SOURCE.md records provenance; neither it nor FETCH is installed.
#
# Source files come from two places:
#   (a) any file sitting in the directory — vendored and pinned;
#   (b) anything listed in FETCH as "<basename> <url>" — fetched fresh on every
#       run into .state/patch-cache/<patch>/ and reused from there when offline.
#
# (b) exists because pinning a file means shipping it, and shipping someone
# else's file drags their licence into this tree. Fetching keeps upstream's
# latest without redistributing it. The trade is real and it cuts the way
# SOURCE.md warned about: a template can now change under a model between runs.
# What replaces the pin is *visibility* — the sha256 of every fetch is kept
# beside the file, and a changed one is reported rather than applied quietly.
#
# The apply check is a sha256 compare, which makes patching self-healing:
# re-download a model and the next run notices the mismatch and re-applies.

PATCH_CACHE="$STATE_DIR/patch-cache"

apply_patch_file() {
    local src="$1" dst="$2"
    # The cached file is normally a symlink into blobs/, which is shared between
    # revisions. Remove it first so the copy replaces the link, never the blob.
    rm -f "$dst"
    cp "$src" "$dst"
}

# fetch_patch_file <patch> <basename> <url>
# Sets FETCHED_PATH to the cached file and returns 0; returns 1 when the file
# could be neither fetched nor found in the cache. Prefers a fresh copy and
# falls back to the cached one when the network is unavailable.
#
# The result comes back through a global rather than stdout on purpose: ok(),
# skip() and note() all print to stdout, so a command substitution around this
# would swallow the drift note instead of showing it — which is precisely the
# report this design exists to produce.
#
# Never fetches under --dry-run: writing the cache would be a mutation.
FETCHED_PATH=""
fetch_patch_file() {
    local _p="$1" _base="$2" _url="$3" _dir _dst _tmp _old _new
    _dir="$PATCH_CACHE/$_p"
    _dst="$_dir/$_base"
    FETCHED_PATH=""

    if is_dry; then
        [ -f "$_dst" ] || return 1
        FETCHED_PATH="$_dst"
        return 0
    fi

    mkdir -p "$_dir" 2>/dev/null || return 1
    _tmp="$_dst.fetching.$$"
    if curl -fsSL --max-time 60 -o "$_tmp" "$_url" 2>/dev/null && [ -s "$_tmp" ]; then
        _new="$(sha_of "$_tmp")"
        _old=""
        [ -f "$_dst" ] && _old="$(sha_of "$_dst")"
        mv -f "$_tmp" "$_dst"
        if [ -n "$_old" ] && [ "$_old" != "$_new" ]; then
            note "$_p/$_base changed upstream since the last run:"
            note "  $(printf '%s' "$_old" | cut -c1-12) -> $(printf '%s' "$_new" | cut -c1-12)"
            note "  $_url"
        fi
        FETCHED_PATH="$_dst"
        return 0
    fi

    rm -f "$_tmp"
    # Leave no empty directory behind after a failed fetch. rmdir refuses a
    # non-empty one, so a surviving cached copy is never at risk.
    rmdir "$_dir" 2>/dev/null || true
    [ -f "$_dst" ] || return 1
    FETCHED_PATH="$_dst"
    return 0
}

# install_patch_file <id> <patch> <src> <snapshot-dir>
install_patch_file() {
    local _id="$1" _patch="$2" _src="$3" _snap="$4" _base _dst
    _base="$(basename "$_src")"
    _dst="$_snap/$_base"
    if [ "$(sha_of "$_src")" = "$(sha_of "$_dst")" ]; then
        ok "$_id — $_patch/$_base"
    else
        do_patch "$_id — $_patch/$_base" apply_patch_file "$_src" "$_dst"
    fi
}

_patched_any=0
for _id in $(manifest_ids); do
    _repo="$(mval "$_id" '.repo' '')"
    _patches="$(yq -r ".models[] | select(.id == \"$_id\") | .patches // [] | .[]" "$MANIFEST" 2>/dev/null || true)"
    [ -n "$_patches" ] || continue
    _patched_any=1

    for _patch in $_patches; do
        _pdir="$ROOT/patches/$_patch"
        if [ ! -d "$_pdir" ]; then
            fail "$_id — patch '$_patch' not found under patches/"
            continue
        fi

        _snapshots="$(snapshot_dirs "$_repo")"
        if [ -z "$_snapshots" ]; then
            skip "$_id — patch '$_patch' pending (model not downloaded yet)"
            continue
        fi

        # Resolve the source files once per patch, not once per snapshot —
        # otherwise a two-revision model fetches everything twice.
        _srcs=""
        for _src in "$_pdir"/*; do
            [ -f "$_src" ] || continue
            case "$(basename "$_src")" in SOURCE.md|FETCH) continue ;; esac
            _srcs="$_srcs$_src
"
        done
        if [ -f "$_pdir/FETCH" ]; then
            while read -r _base _url _rest; do
                case "$_base" in ''|\#*) continue ;; esac
                [ -n "$_url" ] || continue
                if fetch_patch_file "$_patch" "$_base" "$_url"; then
                    _srcs="$_srcs$FETCHED_PATH
"
                elif is_dry; then
                    skip "$_id — $_patch/$_base (would fetch $_url)"
                else
                    fail "$_id — $_patch/$_base could not be fetched and is not cached"
                    note "  $_url"
                fi
            done <"$_pdir/FETCH"
        fi
        [ -n "$_srcs" ] || continue

        while IFS= read -r _snap; do
            [ -n "$_snap" ] || continue
            while IFS= read -r _src; do
                [ -n "$_src" ] || continue
                install_patch_file "$_id" "$_patch" "$_src" "$_snap"
            done <<SRCS
$_srcs
SRCS
        done <<EOF
$_snapshots
EOF
    done
done

[ "$_patched_any" -eq 1 ] || skip "patches (no enabled model declares any)"

# ── GPU pool budget (FINDINGS #16) ───────────────────────────────────────────
# The coder's gpu_memory_utilization is a fraction of Metal's device memory;
# the wired limit is the wall; and what the margin between them must hold
# depends on the machine's RAM bucket (README has the table — on <=32GB,
# nothing besides the coder may stay resident at all). When the pool runs
# out, the failure is not a slow request — the MLX engine aborts mid-turn.
# Advisory only: scripts/gpu-budget.sh computes, this step reports, nobody
# edits the manifest.
if ! _budget="$(LOCALAI_MANIFEST="$MANIFEST" "$ROOT/scripts/gpu-budget.sh" --check 2>&1)"; then
    note "GPU budget: $_budget"
    note "  full arithmetic: scripts/gpu-budget.sh — background: docs/FINDINGS.md #16"
fi

# ── orphaned prefix-cache entries (FINDINGS #14) ─────────────────────────────
# rapid-mlx keys its prefix cache by repo (org--name--hash under
# ~/.cache/rapid-mlx/prefix_cache) and never removes a directory when its
# model leaves the manifest — 2.1GB of retired-model cache was found here
# once. Detection only: it is a shared cache the user may have reasons to
# keep, so name the orphan and the reclaim procedure, touch nothing.
PREFIX_CACHE_ROOT="$HOME/.cache/rapid-mlx/prefix_cache"
if [ -d "$PREFIX_CACHE_ROOT" ]; then
    # Live repos: every enabled entry's repo, plus any repo named inside its
    # flags (the MTP sidecar in --speculative-config must not read as orphaned).
    _live_repos="$(yq -r '.models[] | select(.enabled != false) | .repo' "$MANIFEST" 2>/dev/null | sed 's/:.*$//')
$(yq -r '.models[] | select(.enabled != false) | .flags // ""' "$MANIFEST" 2>/dev/null \
    | grep -oE '"model" *: *"[^"]+"' | sed 's/.*: *"//; s/"$//')"
    for _cachedir in "$PREFIX_CACHE_ROOT"/*/; do
        [ -d "$_cachedir" ] || continue
        _cacherepo="$(basename "$_cachedir" | sed 's/--[0-9a-f]*$//; s|--|/|')"
        if ! printf '%s\n' "$_live_repos" | grep -qxF "$_cacherepo"; then
            _cachesize="$(du -sh "$_cachedir" 2>/dev/null | awk '{print $1}')"
            note "orphaned prefix cache: $_cachedir ($_cachesize) — no enabled model uses $_cacherepo"
            note "  reclaim (service stopped, FINDINGS #14): rm -rf $_cachedir"
        fi
    done
fi
