#!/bin/bash
# Remove Speakeasy from this Mac.
#
# Always removes: the Speakeasy.app bundle (from /Applications, ~/Applications,
# or this repo's dist/) and ~/Library/Logs/Speakeasy.log.
#
# Your data — profiles and settings in
# ~/Library/Application Support/Speakeasy — is KEPT by default so a reinstall
# picks up where you left off. Pass --purge (or answer the prompt) to delete it
# too, along with the ~2.3 GB cached speech model.
#
# Usage:
#   scripts/uninstall.sh            # remove app + log, ask about data/model
#   scripts/uninstall.sh --purge    # also remove profiles, settings, and the model
#   scripts/uninstall.sh --yes      # don't prompt; keep data unless --purge given
#
# Note: this cannot revoke the Microphone / Accessibility / Input Monitoring
# grants (only System Settings can) or delete the "Speakeasy Dev" signing cert.
# Guidance for both is printed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

PURGE=0
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) grep '^#' "$0" | grep -v '^#!' | cut -c3-; exit 0 ;;
        *) echo "error: unknown option '$arg' (see --help)"; exit 1 ;;
    esac
done

APP_SUPPORT="$HOME/Library/Application Support/Speakeasy"
LOG="$HOME/Library/Logs/Speakeasy.log"
MODEL_ID="mlx-community/parakeet-tdt-0.6b-v2"
MODEL_CACHE="$HOME/.cache/huggingface/hub/models--${MODEL_ID//\//--}"

# Ask a yes/no question. Returns 0 for yes. Non-interactive (no TTY, or --yes)
# takes the default: yes only when --purge was passed, else no.
confirm() {
    if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then
        [ "$PURGE" = 1 ]
        return
    fi
    local reply
    read -r -p "$1 [y/N] " reply
    [[ "$reply" =~ ^[Yy] ]]
}

echo "==> Quitting Speakeasy if it is running"
osascript -e 'tell application "System Events" to if exists (process "Speakeasy") then tell application "Speakeasy" to quit' 2>/dev/null || true
pkill -x Speakeasy 2>/dev/null || true

echo "==> Removing the app bundle"
FOUND=0
for APP in "/Applications/Speakeasy.app" "$HOME/Applications/Speakeasy.app" "dist/Speakeasy.app"; do
    if [ -d "$APP" ]; then
        rm -rf "$APP"
        echo "    removed $APP"
        FOUND=1
    fi
done
[ "$FOUND" = 1 ] || echo "    (no Speakeasy.app found in /Applications, ~/Applications, or dist/)"

echo "==> Removing the log"
if [ -f "$LOG" ]; then rm -f "$LOG"; echo "    removed $LOG"; else echo "    (no log at $LOG)"; fi

echo "==> User data (profiles + settings)"
if [ -d "$APP_SUPPORT" ]; then
    if [ "$PURGE" = 1 ] || confirm "    Delete your profiles and settings in $APP_SUPPORT?"; then
        rm -rf "$APP_SUPPORT"
        echo "    removed $APP_SUPPORT"
    else
        echo "    kept $APP_SUPPORT (a reinstall will reuse it)"
    fi
else
    echo "    (none at $APP_SUPPORT)"
fi

echo "==> Cached speech model (~2.3 GB, dev builds only)"
if [ -d "$MODEL_CACHE" ]; then
    if [ "$PURGE" = 1 ] || confirm "    Delete the cached model in $MODEL_CACHE?"; then
        rm -rf "$MODEL_CACHE"
        echo "    removed $MODEL_CACHE"
    else
        echo "    kept $MODEL_CACHE"
    fi
else
    echo "    (none at $MODEL_CACHE)"
fi

cat <<'DONE'

==> Done. Two things this script can't remove for you:

  * Permissions — macOS keeps Speakeasy in System Settings → Privacy &
    Security under Microphone, Accessibility, and Input Monitoring. Remove it
    from each pane if you want it fully gone.

  * Signing cert — the self-signed "Speakeasy Dev" code-signing certificate (if
    you created one) stays in Keychain Access. It's harmless; delete it there
    only if you want to.
DONE
