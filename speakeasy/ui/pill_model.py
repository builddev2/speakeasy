"""What the recording pill and its capture drawer show. Pure: no AppKit, so
every rule is unit-tested; pill_panel.py draws the result."""

from dataclasses import dataclass

SIGNAL_GRACE_SECONDS = 10.0
UNTITLED = "Untitled meeting"
_SYSTEM_FIXES = {
    "requires_macos_14_2": "Unavailable — needs macOS 14.2 or later",
    "permission_denied_or_unavailable":
        "Off — allow Speakeasy in System Settings › Privacy & Security › Screen & System Audio Recording",
    "helper_missing": "Unavailable — reinstall Speakeasy",
    "helper_exited": "Stopped — microphone only for the rest of this meeting",
}


def format_timer(seconds: float) -> str:
    total = max(0, int(seconds))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _failed(health: dict) -> bool:
    exited = health.get("helper_exited") and health.get("helper_exit_reason") != "requested_stop"
    return bool(exited or health.get("system_writer_failed") or health.get("mic_writer_failed"))


def audio_indicator(health: dict, capture_mode: str, elapsed: float) -> str:
    if _failed(health) or health.get("mic_writer_lagged") or health.get("system_writer_lagged"):
        return "warning"
    if health and capture_mode != "mic_and_system":
        return "warning"
    arriving = (health.get("mic_first_buffer") and health.get("system_first_buffer")
                and health.get("system_nonzero_signal"))
    if arriving:
        return "ok"
    return "waiting" if elapsed < SIGNAL_GRACE_SECONDS else "warning"


@dataclass(frozen=True)
class DrawerRow:
    label: str
    value: str
    tone: str


def _system_row(health, capture_mode, status, elapsed) -> DrawerRow:
    exited = health.get("helper_exited") and health.get("helper_exit_reason") != "requested_stop"
    if exited or health.get("system_writer_failed"):
        return DrawerRow("System audio", _SYSTEM_FIXES["helper_exited"], "warning")
    if capture_mode != "mic_and_system":
        return DrawerRow("System audio", _SYSTEM_FIXES.get(status, "Unavailable — microphone only"), "warning")
    if health.get("system_writer_lagged"):
        return DrawerRow("System audio", "All apps · falling behind", "warning")
    if health.get("system_first_buffer") and health.get("system_nonzero_signal"):
        return DrawerRow("System audio", "All apps · working", "ok")
    if elapsed < SIGNAL_GRACE_SECONDS:
        return DrawerRow("System audio", "All apps · waiting for audio", "muted")
    return DrawerRow("System audio", "All apps · no sound yet", "warning")


def _mic_row(health) -> DrawerRow:
    if health.get("mic_writer_failed"):
        return DrawerRow("Microphone", "Writer failed — end and restart the meeting", "warning")
    if health.get("mic_writer_lagged"):
        return DrawerRow("Microphone", "You · falling behind", "warning")
    if health.get("mic_first_buffer"):
        return DrawerRow("Microphone", "You · working", "ok")
    return DrawerRow("Microphone", "You · waiting", "muted")


def drawer_rows(health, capture_mode, system_audio_status, elapsed, event_title):
    return [DrawerRow("Event", event_title or "Link to event…", "action"),
            _mic_row(health),
            _system_row(health, capture_mode, system_audio_status, elapsed),
            DrawerRow("Dictation", "Paused until the meeting ends", "muted")]


@dataclass(frozen=True)
class PillView:
    phase: str
    title: str
    detail: str
    indicator: str
    buttons: tuple


class PillMachine:
    """Engine order (engine.py _process_meeting): on_meeting_saved fires
    before the idle state; a cancel fires no saved event; a failure sets
    meeting_processing_error before the idle state."""

    def __init__(self):
        self.phase = "hidden"
        self.saved_id = None

    def on_state(self, state: str, processing_error):
        if state == "meeting_recording":
            self.phase, self.saved_id = "recording", None
        elif state == "meeting_processing":
            if self.phase not in ("confirm_discard", "cancelling", "saved"):
                self.phase = "processing"
        elif self.phase in ("processing", "confirm_discard", "cancelling", "recording"):
            if self.saved_id is not None:
                self.phase = "saved"
            elif processing_error and self.phase != "cancelling":
                self.phase = "failed"
            else:
                self.phase = "hidden"

    def on_saved(self, meeting_id: str):
        self.phase, self.saved_id = "saved", meeting_id

    def cancel(self):
        if self.phase == "processing":
            self.phase = "confirm_discard"

    def keep(self):
        if self.phase == "confirm_discard":
            self.phase = "processing"

    def discard(self):
        if self.phase == "confirm_discard":
            self.phase = "cancelling"

    def expire_saved(self):
        if self.phase == "saved":
            self.phase, self.saved_id = "hidden", None

    def close(self):
        if self.phase in ("failed", "saved"):
            self.phase, self.saved_id = "hidden", None


_BUTTONS = {"recording": ("notes", "stop"), "processing": ("notes", "cancel"),
            "confirm_discard": ("discard", "keep"), "cancelling": (),
            "saved": ("open",), "failed": ("open_meetings", "close"), "hidden": ()}


def pill_view(machine, *, title, elapsed, health, capture_mode, progress) -> PillView:
    phase = machine.phase
    if phase == "recording":
        return PillView(phase, title or UNTITLED, format_timer(elapsed),
                        audio_indicator(health, capture_mode, elapsed), _BUTTONS[phase])
    texts = {"processing": ("Processing", progress or ""),
             "confirm_discard": ("Discard meeting?", ""),
             "cancelling": ("Cancelling…", ""),
             "saved": ("Saved", ""), "failed": ("Couldn't finish", ""), "hidden": ("", "")}
    head, detail = texts[phase]
    return PillView(phase, head, detail, "none", _BUTTONS[phase])


def clamp_origin(origin, size, screens):
    x, y = origin
    w, h = size
    for sx, sy, sw, sh in screens:
        if sx <= x and sy <= y and x + w <= sx + sw and y + h <= sy + sh:
            return (x, y)
    return None


def default_origin(screen, size):
    sx, sy, sw, sh = screen
    w, h = size
    return (sx + (sw - w) / 2, sy + sh - h - 8)
