"""User-tunable settings for Speakeasy."""

from pathlib import Path

# Key to hold while speaking. Right Command is unused by default in most apps.
# Supported names (see hotkey.py): cmd_r, cmd_l, alt_r, alt_l, ctrl_r, ctrl_l,
# shift_r, shift_l, fn, f13, f14, f15.
HOTKEY = "cmd_r"


def hotkey_name() -> str:
    """Human-readable HOTKEY name for prompts, e.g. 'cmd_r'."""
    return HOTKEY

# Hugging Face model id used by parakeet-mlx (English, punctuated).
MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v2"

# Parakeet expects 16 kHz mono audio.
SAMPLE_RATE = 16_000

# Recordings shorter than this are treated as accidental taps and dropped.
MIN_DURATION_SECONDS = 0.3

# How long to wait for the recorder to stop before giving up on it. A wedged
# CoreAudio call must not freeze the control thread (and with it the hotkey),
# so past this the stream is abandoned and the mic forcibly released.
RECORDER_STOP_TIMEOUT_SECONDS = 1.5

# Play system sounds when recording starts/stops.
SOUNDS_ENABLED = True
SOUND_START = "/System/Library/Sounds/Pop.aiff"
SOUND_STOP = "/System/Library/Sounds/Bottle.aiff"

# --- Meeting transcription ---------------------------------------------------
# Long-form audio is transcribed in overlapping chunks so a 2-hour meeting
# never has to fit through the model in one shot; the overlap gives the token
# mergers (parakeet_mlx.alignment) enough shared context to stitch chunks.
MEETING_CHUNK_SECONDS = 120.0
MEETING_OVERLAP_SECONDS = 15.0

# Hard cap on a meeting's spooled audio. Frames past this are dropped so an
# abandoned recording can't fill the disk (~115 MB/hour of int16 WAV).
MEETING_MAX_SECONDS = 3 * 3600

# Speaker-clustering sensitivity for diarization (sherpa-onnx FastClustering,
# speaker count unknown). Lower = more willing to split voices apart. 0.5 is
# the library default, tuned for clean audio; real-world conversation (cross-
# talk, room noise, kids' more variable pitch) needs more headroom or it
# over-splits badly — a 4-person family meal fragmented into 17 "speakers"
# at 0.5. 0.7 trades a little of that precision for much better recall on
# noisy/casual audio (occasionally merging two similar-sounding speakers).
DIARIZATION_THRESHOLD = 0.7

# Diarization tokens shorter than these are treated as noise (seconds of
# speech-on / silence-off, passed straight to sherpa-onnx).
DIARIZATION_MIN_ON = 0.3
DIARIZATION_MIN_OFF = 0.5

SOUND_MEETING_START = "/System/Library/Sounds/Glass.aiff"
SOUND_MEETING_END = "/System/Library/Sounds/Submarine.aiff"

# Delay after synthesizing Cmd+V before restoring the previous clipboard.
PASTE_SETTLE_SECONDS = 0.15

# Pause after writing the pasteboard before synthesizing Cmd+V, so the
# frontmost app observes the new clipboard contents before it reads them.
CLIPBOARD_SETTLE_SECONDS = 0.02

# Old in-repo profiles location. Profiles now live in
# ~/Library/Application Support/Speakeasy/profiles (see settings.py);
# anything found here is copied over once, the first time that dir is made.
LEGACY_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

# --- Waveform overlay -------------------------------------------------------
# Set to False to disable the visual entirely (sounds + terminal log only).
OVERLAY_ENABLED = True

OVERLAY_BAR_COUNT = 7
OVERLAY_BAR_WIDTH = 6.0       # pt
OVERLAY_BAR_GAP = 7.0         # pt between bars
OVERLAY_MAX_BAR_HEIGHT = 34.0 # pt at full voice level
OVERLAY_MIN_BAR_HEIGHT = 4.0  # pt resting dot
OVERLAY_BOTTOM_OFFSET = 83.0  # pt above the bottom of the screen
OVERLAY_ALPHA = 0.4           # bar translucency (lower = more transparent/subtle)
