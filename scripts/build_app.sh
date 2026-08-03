#!/bin/bash
# Build the standalone Speakeasy.app into dist/, and optionally install it.
#
# Steps: frontend + PyInstaller + native system-audio helper → copy bundled
# models/resources → sanity-check the bundle → codesign.
#
# Usage: scripts/build_app.sh [--install]
#   --install   also update /Applications/Speakeasy.app in place (see below).
#
# Signing: set SIGN_ID to a code-signing identity in your keychain
# (default "Speakeasy Dev", a self-signed cert you create once in Keychain
# Access → Certificate Assistant → Create a Certificate → Code Signing).
# A stable identity keeps the Microphone / Accessibility / Input Monitoring
# grants across rebuilds; without one we fall back to ad-hoc signing and
# macOS treats every rebuild as a brand-new app (re-grant all three).
#
# Installing: --install updates the existing bundle in place (rsync) rather
# than deleting and recreating it. TCC keys the Accessibility / Input
# Monitoring grants on the code's designated requirement, but replacing the
# whole .app can drop the existing entry and force a re-grant; overwriting the
# contents of the same bundle keeps it. (The very first install after changing
# the signing identity still re-prompts once — that's unavoidable.)
set -euo pipefail
cd "$(dirname "$0")/.."

INSTALL=0
for arg in "$@"; do
    case "$arg" in
        --install) INSTALL=1 ;;
        *) echo "error: unknown option '$arg' (usage: $0 [--install])" >&2; exit 2 ;;
    esac
done

VENV=.venv/bin
MODEL_ID="mlx-community/parakeet-tdt-0.6b-v2"
APP=dist/Speakeasy.app
DEST=/Applications/Speakeasy.app

[ "$(uname -m)" = "arm64" ] || { echo "error: Apple Silicon required (MLX)"; exit 1; }
[ -x "$VENV/python" ] || { echo "error: .venv missing — see README first-time setup"; exit 1; }
"$VENV/python" -c "import PyInstaller" 2>/dev/null \
    || { echo "installing build deps..."; "$VENV/pip" install -q -r requirements-dev.txt; }

# Locate the cached model before spending minutes on the build.
SNAPSHOTS="$HOME/.cache/huggingface/hub/models--${MODEL_ID//\//--}/snapshots"
SNAP=$(ls -d "$SNAPSHOTS"/*/ 2>/dev/null | head -1 || true)
if [ -z "$SNAP" ] || [ ! -f "$SNAP/config.json" ] || [ ! -e "$SNAP/model.safetensors" ]; then
    echo "error: model not in the HF cache — run the app once from the venv to download it:"
    echo "  $VENV/python -m speakeasy --cli"
    exit 1
fi

# Diarization models (meetings feature) must be fetched before building.
DIAR=models/diarization
if [ ! -f "$DIAR/segmentation.onnx" ] || [ ! -f "$DIAR/embedding.onnx" ]; then
    echo "error: diarization models missing — fetch them once with:"
    echo "  scripts/fetch_diarization_models.sh"
    exit 1
fi

[ -f assets/Speakeasy.icns ] || "$VENV/python" scripts/make_icon.py

# Build the web UI (needs node/npm once at build time — never at runtime).
if ! command -v npm >/dev/null 2>&1; then
    echo "error: npm not found — install Node.js; it is needed at build time to build the UI"
    exit 1
fi
echo "Building frontend…"
npm --prefix frontend ci
npm --prefix frontend run build
if [ ! -f frontend/dist/dock.html ]; then
    echo "error: frontend build produced no dist/dock.html"
    exit 1
fi

echo "==> PyInstaller build"
rm -rf build dist
"$VENV/python" -m PyInstaller --noconfirm --distpath dist --workpath build \
    packaging/Speakeasy.spec

echo "==> Building system-audio helper"
scripts/build_system_audio_helper.sh

echo "==> Bundling model ($(du -sh "$SNAP" | cut -f1))"
mkdir -p "$APP/Contents/Resources/model"
rsync -aL "$SNAP/config.json" "$SNAP/model.safetensors" "$APP/Contents/Resources/model/"

echo "==> Bundling diarization models ($(du -sh "$DIAR" | cut -f1))"
mkdir -p "$APP/Contents/Resources/diarization"
rsync -aL "$DIAR/segmentation.onnx" "$DIAR/embedding.onnx" "$APP/Contents/Resources/diarization/"

echo "==> Bundling frontend"
rsync -a --delete frontend/dist/ "$APP/Contents/Resources/frontend/"

echo "==> Bundling system-audio helper"
mkdir -p "$APP/Contents/Resources/native"
rsync -a build/native/SpeakeasySystemAudioCapture "$APP/Contents/Resources/native/"

echo "==> Sanity checks"
METALLIB=$(find "$APP" -path "*mlx/lib/mlx.metallib" | head -1)
[ -n "$METALLIB" ] || { echo "error: mlx.metallib missing from bundle"; exit 1; }
[ -e "$(dirname "$METALLIB")/libmlx.dylib" ] \
    || { echo "error: mlx.metallib not adjacent to libmlx.dylib"; exit 1; }
SHERPA=$(find "$APP" -name "*_sherpa_onnx*" | head -1)
[ -n "$SHERPA" ] || { echo "error: sherpa-onnx extension missing from bundle"; exit 1; }
[ -f "$APP/Contents/Resources/diarization/segmentation.onnx" ] \
    && [ -f "$APP/Contents/Resources/diarization/embedding.onnx" ] \
    || { echo "error: diarization models missing from bundle"; exit 1; }
[ -f "$APP/Contents/Resources/frontend/dock.html" ] || { echo "error: frontend missing from bundle"; exit 1; }
[ -x "$APP/Contents/Resources/native/SpeakeasySystemAudioCapture" ] \
    || { echo "error: system-audio helper missing from bundle"; exit 1; }
LEFTOVERS=$(find "$APP" \( -iname "*librosa*" -o -iname "*numba*" -o -iname "*llvmlite*" \) | head -3)
[ -z "$LEFTOVERS" ] || { echo "error: excluded packages leaked into bundle:"; echo "$LEFTOVERS"; exit 1; }

echo "==> Signing"
SIGN_ID="${SIGN_ID:-Speakeasy Dev}"
if security find-identity -v -p codesigning 2>/dev/null | grep -q "$SIGN_ID"; then
    codesign --force --deep --sign "$SIGN_ID" "$APP"
    echo "signed with identity: $SIGN_ID"
else
    codesign --force --deep --sign - "$APP"
    echo "WARNING: identity '$SIGN_ID' not in keychain — ad-hoc signed."
    echo "  macOS will ask for Microphone/Accessibility/Input Monitoring again"
    echo "  after every rebuild. Create the cert once (see README) to stop that."
fi
codesign --verify --deep --strict "$APP"

echo "==> Done: $APP ($(du -sh "$APP" | cut -f1))"

if [ "$INSTALL" = 1 ]; then
    echo "==> Installing to $DEST"
    # Quit a running instance first — replacing files under a live bundle can
    # crash it mid-write.
    if pgrep -f "$DEST/Contents/MacOS/Speakeasy" >/dev/null 2>&1; then
        echo "  quitting the running Speakeasy…"
        osascript -e 'quit app "Speakeasy"' 2>/dev/null || true
        for _ in 1 2 3 4 5 6; do
            pgrep -f "$DEST/Contents/MacOS/Speakeasy" >/dev/null 2>&1 || break
            sleep 0.5
        done
        pkill -f "$DEST/Contents/MacOS/Speakeasy" 2>/dev/null || true
    fi
    if [ -d "$DEST" ]; then
        # Overwrite the existing bundle's contents in place (same bundle dir)
        # so the TCC grants survive; --delete clears files dropped since the
        # last build. rsync copies the signed bundle verbatim, so the signature
        # stays valid — no re-sign needed.
        echo "  updating in place (keeps Input Monitoring / Accessibility grants)…"
        rsync -a --delete "$APP/" "$DEST/"
    else
        echo "  no existing install — copying fresh…"
        cp -R "$APP" "$DEST"
    fi
    codesign --verify --deep --strict "$DEST"
    echo "installed: $DEST"
    echo "Launch:  open $DEST"
else
    echo "Try it:   open $APP"
    echo "Install:  $0 --install   (updates $DEST in place, preserving TCC grants)"
fi
