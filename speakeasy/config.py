"""User-tunable settings for Speakeasy."""

from pynput import keyboard

# Key to hold while speaking. Right Command is unused by default in most apps.
# Other options: keyboard.Key.alt_r, keyboard.Key.alt_l, keyboard.Key.f13, ...
HOTKEY = keyboard.Key.cmd_r

# Hugging Face model id used by parakeet-mlx (English, punctuated).
MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v2"

# Parakeet expects 16 kHz mono audio.
SAMPLE_RATE = 16_000

# Recordings shorter than this are treated as accidental taps and dropped.
MIN_DURATION_SECONDS = 0.3

# Play system sounds when recording starts/stops.
SOUNDS_ENABLED = True
SOUND_START = "/System/Library/Sounds/Pop.aiff"
SOUND_STOP = "/System/Library/Sounds/Bottle.aiff"

# Delay after synthesizing Cmd+V before restoring the previous clipboard.
PASTE_SETTLE_SECONDS = 0.15

# --- Waveform overlay -------------------------------------------------------
# Set to False to disable the visual entirely (sounds + terminal log only).
OVERLAY_ENABLED = True

OVERLAY_BAR_COUNT = 7
OVERLAY_BAR_WIDTH = 6.0       # pt
OVERLAY_BAR_GAP = 7.0         # pt between bars
OVERLAY_MAX_BAR_HEIGHT = 34.0 # pt at full voice level
OVERLAY_MIN_BAR_HEIGHT = 4.0  # pt resting dot
OVERLAY_BOTTOM_OFFSET = 83.0  # pt above the bottom of the screen
OVERLAY_ALPHA = 0.7           # bar translucency (glassy look)
