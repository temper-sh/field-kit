# steps/45-extensions.sh — install the Pi extensions that expose specialist models as tools.
#
# Pi auto-discovers ~/.pi/agent/extensions/*.ts globally.
#
# An extension installs only when the manifest carries the model entry it
# consumes (the entry ↔ extension edge from PLAN §10, enforced from the
# manifest): a day-two hand-edit that deletes an entry also removes its tool
# on the next run, and a manifest that never had the entry never grows the
# tool. The edges are hard requirements in the extension sources — each
# extension's default model id.
#
# Sourced by setup.sh.
# shellcheck shell=bash

section "extensions"

PI_EXT_DIR="$HOME/.pi/agent/extensions"
SRC_DIR="$ROOT/pi-extensions"

install_extension() {
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    backup_file "$dst"
    cp "$src" "$dst"
}

remove_extension() {
    backup_file "$1"
    rm -f "$1"
}

# The manifest entry each extension requires; empty = no model dependency.
extension_requires() {
    case "$1" in
        project-search.ts) echo "rerank-qwen3-0.6b" ;;
        extract-json.ts)   echo "extract-nuextract3" ;;
        *)                 echo "" ;;
    esac
}

# Present and not `enabled: false` — the same idiom the generator renders by,
# so tool and backend can never disagree about what is deployed.
manifest_has_enabled() {
    [ -n "$(yq -r ".models[] | select(.enabled != false) | select(.id == \"$1\") | .id" "$MANIFEST" 2>/dev/null)" ]
}

if [ ! -d "$SRC_DIR" ]; then
    skip "no pi-extensions/ directory"
    return 0
fi

_found=0
for _src in "$SRC_DIR"/*.ts; do
    [ -f "$_src" ] || continue
    _found=1
    _name="$(basename "$_src")"
    _dst="$PI_EXT_DIR/$_name"

    _req="$(extension_requires "$_name")"
    if [ -n "$_req" ] && ! manifest_has_enabled "$_req"; then
        if [ -f "$_dst" ]; then
            do_change "$_name — $_req is not in the manifest, removing the tool" remove_extension "$_dst"
        else
            skip "$_name ($_req is not in the manifest)"
        fi
        continue
    fi

    if [ ! -f "$_dst" ]; then
        do_install "$_name" install_extension "$_src" "$_dst"
    elif [ "$(sha_of "$_src")" = "$(sha_of "$_dst")" ]; then
        ok "$_name"
    else
        do_patch "$_name" install_extension "$_src" "$_dst"
    fi
done

[ "$_found" -eq 1 ] || skip "no extensions to install"
