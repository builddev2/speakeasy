#!/bin/bash
# Build the standalone Speakeasy.app into dist/.
#
# Steps: PyInstaller bundle → copy the speech model from the local HF cache
# into Contents/Resources/model → sanity-check the bundle → codesign.
#
# Signing: set SIGN_ID to a code-signing identity in your keychain
# (default "Speakeasy Dev", a self-signed cert you create once in Keychain
# Access → Certificate Assistant → Create a Certificate → Code Signing).
# A stable identity keeps the Microphone / Accessibility / Input Monitoring
# grants across rebuilds; without one we fall back to ad-hoc signing and
# macOS treats every rebuild as a brand-new app (re-grant all three).
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=.venv/bin
MODEL_ID="mlx-community/parakeet-tdt-0.6b-v2"
APP=dist/Speakeasy.app

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

[ -f assets/Speakeasy.icns ] || "$VENV/python" scripts/make_icon.py

echo "==> PyInstaller build"
rm -rf build dist
"$VENV/python" -m PyInstaller --noconfirm --distpath dist --workpath build \
    packaging/Speakeasy.spec

echo "==> Bundling model ($(du -sh "$SNAP" | cut -f1))"
mkdir -p "$APP/Contents/Resources/model"
rsync -aL "$SNAP/config.json" "$SNAP/model.safetensors" "$APP/Contents/Resources/model/"

echo "==> Sanity checks"
METALLIB=$(find "$APP" -path "*mlx/lib/mlx.metallib" | head -1)
[ -n "$METALLIB" ] || { echo "error: mlx.metallib missing from bundle"; exit 1; }
[ -e "$(dirname "$METALLIB")/libmlx.dylib" ] \
    || { echo "error: mlx.metallib not adjacent to libmlx.dylib"; exit 1; }
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
echo "Try it:   open $APP"
echo "Install:  mv $APP /Applications/"
