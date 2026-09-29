"""MeetingOptions lives apart from engine.py so the UI bridges can build one
without importing the engine's heavy audio/model modules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MeetingOptions:
    # Remote speakers in dual-track mode. In mic-only fallback, source
    # separation is impossible, so this constrains every voice audible on mic.
    expected_speaker_count: int | None = None
    expected_voice_profile_names: tuple[str, ...] = ()
    system_audio_pid: int | None = None
    calendar_event_key: str | None = None

    def __post_init__(self) -> None:
        if (
            self.expected_speaker_count is not None
            and not 1 <= self.expected_speaker_count <= 20
        ):
            raise ValueError("Expected speaker count must be between 1 and 20.")
        if self.system_audio_pid is not None and (
            not isinstance(self.system_audio_pid, int)
            or isinstance(self.system_audio_pid, bool)
            or self.system_audio_pid <= 0
        ):
            raise ValueError("Selected application is no longer available.")
        if self.calendar_event_key is not None and (
            not isinstance(self.calendar_event_key, str)
            or not 0 < len(self.calendar_event_key) <= 300
        ):
            raise ValueError("Unknown calendar event.")
