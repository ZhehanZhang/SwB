#!/bin/bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || "$1" != *.app ]]; then
    echo "Usage: $0 OUTPUT.app [VERSION]" >&2
    exit 2
fi
if [[ "$(uname -s)" != Darwin ]]; then
    echo "Build SwB Companion on macOS." >&2
    exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
version="${2:-$(awk -F '"' '/^\[project\]$/ { project = 1; next }
    /^\[/ { project = 0 }
    project && /^version[[:space:]]*=/ { print $2; exit }' "$here/../../pyproject.toml")}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Use a numeric major.minor.patch bundle version." >&2
    exit 2
fi

read -r -a architectures <<< "${SWB_COMPANION_ARCHS:-arm64 x86_64}"
built=()
swift_flags=(-swift-version 5 -O)
if [[ "${SWB_BLE_SMOKE_TEST:-0}" == 1 ]]; then
    swift_flags+=(-D SWB_BLE_SMOKE_TEST)
fi
if [[ "${#architectures[@]}" == 0 ]]; then
    echo "Choose at least one architecture." >&2
    exit 2
fi
output="$1"
[[ "$output" == /* ]] || output="$PWD/$output"
parent="$(dirname "$output")"
mkdir -p "$parent"
if [[ -e "$output" || -L "$output" ]]; then
    echo "Choose a new app output path." >&2
    exit 2
fi
stage="$(mktemp -d "$parent/.swb-companion.XXXXXXXX")"
trap 'rm -rf "$stage"' EXIT
bundle="$stage/$(basename "$output")"
mkdir -p "$bundle/Contents/MacOS"
cp "$here/Info.plist" "$bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $version" "$bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $version" "$bundle/Contents/Info.plist"
/usr/bin/plutil -lint "$bundle/Contents/Info.plist"

sdk="$(xcrun --sdk macosx --show-sdk-path)"
for arch in "${architectures[@]}"; do
    case "$arch" in
        arm64|x86_64) ;;
        *) echo "Unsupported architecture: $arch" >&2; exit 2 ;;
    esac
    binary="$stage/SwBCompanion-$arch"
    xcrun --sdk macosx swiftc "${swift_flags[@]}" -sdk "$sdk" -target "$arch-apple-macos11.0" \
        -o "$binary" "$here"/*.swift
    built+=("$binary")
done
executable="$bundle/Contents/MacOS/SwBCompanion"
if [[ "${#built[@]}" == 1 ]]; then
    mv "${built[0]}" "$executable"
else
    xcrun lipo -create "${built[@]}" -output "$executable"
fi
mv "$bundle" "$output"
