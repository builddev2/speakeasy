#!/bin/bash
# Fetch the two speaker-diarization ONNX models into models/diarization/.
#
# This is the ONLY network step the meetings feature has, and it runs at
# dev/build time — never at runtime (mirroring how the speech model is
# cached once and then bundled). Checksums are pinned: a swapped artifact
# upstream fails loudly instead of shipping.
#
# Idempotent: verified files are kept, anything missing or corrupt is
# re-downloaded.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=models/diarization
SEG_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
# Note: "recongition" is a real typo in the upstream release tag.
EMB_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx"
SEG_SHA="220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
EMB_SHA="ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e"

mkdir -p "$DEST"

verify() { # verify <file> <sha256>
    [ -f "$1" ] && [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$2" ]
}

if verify "$DEST/segmentation.onnx" "$SEG_SHA"; then
    echo "segmentation.onnx already present and verified"
else
    echo "==> Fetching pyannote segmentation model"
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    curl -fsSL -o "$TMP/seg.tar.bz2" "$SEG_URL"
    tar xjf "$TMP/seg.tar.bz2" -C "$TMP"
    mv "$TMP/sherpa-onnx-pyannote-segmentation-3-0/model.onnx" "$DEST/segmentation.onnx"
    verify "$DEST/segmentation.onnx" "$SEG_SHA" \
        || { echo "error: segmentation.onnx checksum mismatch" >&2; rm -f "$DEST/segmentation.onnx"; exit 1; }
fi

if verify "$DEST/embedding.onnx" "$EMB_SHA"; then
    echo "embedding.onnx already present and verified"
else
    echo "==> Fetching NeMo titanet speaker-embedding model"
    curl -fsSL -o "$DEST/embedding.onnx" "$EMB_URL"
    verify "$DEST/embedding.onnx" "$EMB_SHA" \
        || { echo "error: embedding.onnx checksum mismatch" >&2; rm -f "$DEST/embedding.onnx"; exit 1; }
fi

echo "Done: $(du -sh "$DEST" | cut -f1) in $DEST"
