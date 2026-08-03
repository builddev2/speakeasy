#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=build/native/SpeakeasySystemAudioCapture
CACHE=build/native/module-cache
DEFAULT_SDK=$(xcrun --sdk macosx --show-sdk-path)

# A versioned 14/15 SDK is sufficient for the macOS 14.2 process-tap API and
# avoids a partially updated Command Line Tools install where the newest SDK
# and swiftc have different patch versions. Callers can override explicitly.
if [ -n "${SYSTEM_AUDIO_SDK:-}" ]; then
    SDK=$SYSTEM_AUDIO_SDK
else
    SDK=$(find "$(dirname "$DEFAULT_SDK")" -maxdepth 1 \
        \( -name 'MacOSX14*.sdk' -o -name 'MacOSX15*.sdk' \) \
        -type d 2>/dev/null | sort -V | tail -1)
    SDK=${SDK:-$DEFAULT_SDK}
fi

mkdir -p "$(dirname "$OUT")" "$CACHE"
CLANG_MODULE_CACHE_PATH="$CACHE" SWIFT_MODULECACHE_PATH="$CACHE" \
    xcrun swiftc -O -parse-as-library \
    -sdk "$SDK" -target arm64-apple-macos14.0 \
    -framework CoreAudio -framework Foundation \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
    -Xlinker native/SystemAudioCapture-Info.plist \
    -o "$OUT" native/SystemAudioCapture.swift

echo "Built $OUT (deployment target macOS 14.0; capture feature requires 14.2+)"
