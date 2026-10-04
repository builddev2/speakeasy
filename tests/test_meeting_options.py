import pytest

from speakeasy import settings
from speakeasy.meeting_options import MeetingOptions, default_options


def test_defaults_are_all_system_audio_and_auto_speakers():
    assert default_options(identify_voices=False) == MeetingOptions()


def test_event_key_is_passed_through():
    assert default_options("ev-1", identify_voices=False) == MeetingOptions(calendar_event_key="ev-1")


def test_identify_voices_uses_every_enrolled_voice():
    opts = default_options(identify_voices=True, voice_names=["Ana", "Bo"])
    assert opts.expected_voice_profile_names == ("Ana", "Bo")
    assert opts.system_audio_pid is None and opts.expected_speaker_count is None


def test_identify_voices_off_ignores_enrolled_voices():
    assert default_options(identify_voices=False, voice_names=["Ana"]).expected_voice_profile_names == ()


def test_reads_the_setting_when_not_given(monkeypatch):
    settings.set_identify_voices(True)
    import speakeasy.voice_profiles as vp
    monkeypatch.setattr(vp.VoiceProfileStore, "names", lambda self: ["Cy"])
    assert default_options().expected_voice_profile_names == ("Cy",)
