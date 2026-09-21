#!/bin/sh
# Bootstrap without Homebrew, a compiler, or an existing Python installation.
set -eu
umask 077
field_kit_directory=$(CDPATH='' cd "$(dirname "$0")" && pwd -P)
install_only=no
case "$#:$*" in
    0:) ;;
    '1:--install-only') install_only=yes ;;
    '1:--help') printf '%s\n' 'Usage: ./setup.sh [--install-only]' 'Install the signed Temper release and local Python, then open Field Kit.'; exit 0 ;;
    *) printf '%s\n' 'Usage: ./setup.sh [--install-only]' >&2; exit 2 ;;
esac
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || {
    printf '%s\n' 'Field Kit currently requires an Apple Silicon Mac. Open Terminal natively if using Rosetta.' >&2
    exit 1
}
field_kit_local="$field_kit_directory/.local"
[ ! -L "$field_kit_local" ] || { printf '%s\n' 'Refusing a symlink at .local.' >&2; exit 1; }
mkdir -p "$field_kit_local"
mkdir "$field_kit_local/setup.lock" 2>/dev/null || {
    printf '%s\n' 'Setup is already running or was interrupted. If no setup is running, remove .local/setup.lock and retry.' >&2
    exit 1
}
field_kit_stage=
cleanup_setup() {
    if [ -n "$field_kit_stage" ]; then rm -rf "$field_kit_stage"; fi
    rmdir "$field_kit_local/setup.lock"
}
trap cleanup_setup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
field_kit_stage=$(mktemp -d "$field_kit_local/.setup.XXXXXX")
field_kit_archive=temper_0.1.0-alpha.7_darwin_arm64.zip
field_kit_sha=a7761219481852b200f3e810b6cc4530cd5ab40f73a9f9d453c2fd7dad905021
field_kit_zip="$field_kit_local/$field_kit_archive"
[ ! -L "$field_kit_zip" ] || { printf '%s\n' 'Refusing a symlink at the release cache.' >&2; exit 1; }
printf '%s\n' 'Installing Temper 0.1.0-alpha.7 and Python locally (about 31 MB of downloads).' 'No administrator access is needed. Model downloads and inference require confirmation next.'
if [ ! -f "$field_kit_zip" ]; then
    curl --fail --location --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 20 --max-time 600 \
        "https://github.com/temper-sh/temper/releases/download/v0.1.0-alpha.7/$field_kit_archive" \
        --output "$field_kit_stage/$field_kit_archive"
    [ "$(shasum -a 256 "$field_kit_stage/$field_kit_archive" | cut -d ' ' -f 1)" = "$field_kit_sha" ] || {
        printf '%s\n' 'Temper release checksum mismatch; nothing was installed.' >&2; exit 1;
    }
    mv "$field_kit_stage/$field_kit_archive" "$field_kit_zip"
fi
[ "$(shasum -a 256 "$field_kit_zip" | cut -d ' ' -f 1)" = "$field_kit_sha" ] || {
    printf '%s\n' 'Cached Temper checksum mismatch. Remove .local/temper_0.1.0-alpha.7_darwin_arm64.zip and run setup again.' >&2; exit 1;
}
ditto -x -k "$field_kit_zip" "$field_kit_stage"
field_kit_binary="$field_kit_stage/temper_0.1.0-alpha.7_darwin_arm64/temper"
codesign --verify --strict -R='anchor apple generic and certificate leaf[subject.OU] = "5VQ2HDTPN4"' "$field_kit_binary"
[ ! -L "$field_kit_local/temper" ] || { printf '%s\n' 'Refusing a symlink at the Temper destination.' >&2; exit 1; }
if [ ! -f "$field_kit_local/temper" ] || ! cmp -s "$field_kit_binary" "$field_kit_local/temper"; then
    mv "$field_kit_binary" "$field_kit_local/temper"
fi
# The private parent already protects this tree. Temper checks archive modes;
# preserve their group/other bits during extraction instead of masking them.
umask 022
field_kit_waits=0
while ! "$field_kit_local/temper" software install --root "$field_kit_local/python" --installation field-kit-python \
    --lock "$field_kit_directory/runtime/python-macos-arm64.lock.yaml" > "$field_kit_stage/python-install" 2>&1; do
    if grep -q 'operation is held by a live invocation' "$field_kit_stage/python-install" && [ "$field_kit_waits" -lt 31 ]; then
        if [ "$field_kit_waits" -eq 0 ]; then printf '%s\n' 'Waiting for the previous Python installation lease to expire (at most 15 minutes) ...'; fi
        field_kit_waits=$((field_kit_waits + 1))
        sleep 30
    else
        cat "$field_kit_stage/python-install" >&2
        exit 1
    fi
done
printf '%s\n' 'Python installation verified.'
"$field_kit_local/temper" software check --root "$field_kit_local/python" --installation field-kit-python \
    --lock "$field_kit_directory/runtime/python-macos-arm64.lock.yaml" > "$field_kit_stage/python-check"
field_kit_python=$(awk '/^UNIT upstream-release:field-kit-python:cpython exact / {sub(/^.* location=/, ""); sub(/ ownership=.*$/, ""); print}' "$field_kit_stage/python-check")/bin/python3
[ -x "$field_kit_python" ] || { printf '%s\n' 'Temper did not return a verified Python installation.' >&2; exit 1; }
"$field_kit_python" -B -S -c 'import sys; assert sys.version_info >= (3, 9)'
printf '%s\n' "$field_kit_python" > "$field_kit_stage/python-path"
mv "$field_kit_stage/python-path" "$field_kit_local/python-path"
"$field_kit_directory/field-kit" verify > /dev/null
printf '%s\n' 'Setup complete. Everything is inside this clone.'
cleanup_setup
trap - EXIT INT TERM
if ! "$field_kit_local/temper" help | grep -q 'temper execution prepare'; then
    printf '%s\n' 'This development study requires newer Temper execution commands than the bootstrap release.' 'Use a matching development build: ./field-kit contribute --temper /absolute/path/to/temper' 'Keep dispatched studies in their original checkout.'
    exit 0
fi
if [ "$install_only" = no ] && [ -t 0 ]; then exec "$field_kit_directory/field-kit" contribute; fi
printf '%s\n' 'Run ./field-kit to start or resume an experiment.'
