# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone Speakeasy.app.

Build with scripts/build_app.sh (it also copies the speech model into the
bundle and signs it) — not with bare pyinstaller.

Key decisions:
- collect_all('mlx') keeps mlx/lib/mlx.metallib next to libmlx.dylib in the
  bundle; Metal kernel loading fails if that package-relative layout breaks.
- librosa (and its numba/llvmlite/scipy tree) is excluded: the only call
  Speakeasy's path needs is replaced by speakeasy/_mel_shim.py, which
  activates automatically when `import librosa` fails. This cuts hundreds of
  MB and the most freeze-fragile dependencies.
- The 2.3 GB model is NOT a datas entry — the build script rsyncs it into
  Contents/Resources/model after the build, so rebuilds don't recopy it.
"""

from PyInstaller.utils.hooks import collect_all

mlx_datas, mlx_bins, mlx_hidden = collect_all("mlx")
pk_datas, pk_bins, pk_hidden = collect_all("parakeet_mlx")
# permissions.py does `from ApplicationServices import AXIsProcessTrusted`;
# without collect_all only HIServices' .so is pulled in and GUI mode crashes.
as_datas, as_bins, as_hidden = collect_all("ApplicationServices")
# Speaker diarization: the sherpa-onnx wheel carries its own onnxruntime
# dylibs; collect_all keeps them next to the extension module. The two ONNX
# model files are NOT datas — build_app.sh rsyncs them into
# Contents/Resources/diarization (same pattern as the speech model).
so_datas, so_bins, so_hidden = collect_all("sherpa_onnx")

a = Analysis(
    ["../launcher.py"],
    pathex=[".."],
    binaries=mlx_bins + pk_bins + as_bins + so_bins,
    datas=mlx_datas + pk_datas + as_datas + so_datas,
    hiddenimports=(
        mlx_hidden
        + pk_hidden
        + as_hidden
        + so_hidden
        + [
            "dacite",
            # imported inside functions, so PyInstaller can't see them statically
            "speakeasy.ui.menubar",
            "speakeasy.ui.training_window",
            "speakeasy.ui.meetings_window",
            "speakeasy.ui.main_window",
            "speakeasy.ui.permissions",
            "speakeasy.ui.overlay",
            "speakeasy.cli",
            "speakeasy.diarizer",
        ]
    ),
    excludes=[
        "librosa",
        "numba",
        "llvmlite",
        "scipy",
        "sklearn",
        "scikit-learn",
        "soundfile",
        "audioread",
        "soxr",
        "pooch",
        "matplotlib",
        "pandas",
        "PIL",
        "IPython",
        "tkinter",
        "pytest",
        "setuptools",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Speakeasy",
    console=False,
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Speakeasy",
)

app = BUNDLE(
    coll,
    name="Speakeasy.app",
    icon="../assets/Speakeasy.icns",
    bundle_identifier="com.jasonchiu.speakeasy",
    info_plist={
        # Regular (not agent/LSUIElement) app: normal Dock icon + app-switcher
        # entry, so ui/main_window.py is reachable if the menu-bar status
        # item is ever hidden by overflow. menubar.py's run_app() sets the
        # same NSApplicationActivationPolicyRegular at runtime; setting it
        # here too avoids a Dock-icon flash/promotion right after launch.
        "LSUIElement": False,
        "LSMinimumSystemVersion": "14.0",  # MLX floor
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "NSMicrophoneUsageDescription": (
            "Speakeasy records your voice while you hold the hotkey, "
            "transcribes it on-device, and types it at your cursor. "
            "Meeting recordings are also transcribed on-device, and the "
            "audio is deleted as soon as the transcript is saved. "
            "Audio never leaves this Mac."
        ),
        "NSHumanReadableCopyright": "Local-only dictation. No network, ever.",
    },
)
