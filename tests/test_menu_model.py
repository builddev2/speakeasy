import pytest

from speakeasy.ui.menu_model import menu_flags


@pytest.mark.parametrize("state,flags", [
    ("ready", dict(next_up=True, meeting_notes=False, retry_mic=False, cancel=False)),
    ("meeting_recording", dict(next_up=False, meeting_notes=True, retry_mic=False, cancel=False)),
    ("meeting_processing", dict(next_up=False, meeting_notes=True, retry_mic=False, cancel=True)),
    ("meeting_recording+1", dict(next_up=False, meeting_notes=True, retry_mic=False, cancel=True)),
    ("mic_failed", dict(next_up=False, meeting_notes=False, retry_mic=True, cancel=False)),
    ("loading", dict(next_up=False, meeting_notes=False, retry_mic=False, cancel=False)),
])
def test_menu_flags(state, flags):
    pending = 1 if state in ("meeting_processing", "meeting_recording+1") else 0
    assert menu_flags(state.split("+")[0], pending) == flags


from speakeasy.ui.menu_model import processing_suffix, quit_warning, start_enabled


def test_start_enabled_during_processing():
    assert start_enabled("ready") and start_enabled("meeting_processing")
    assert not start_enabled("meeting_recording") and not start_enabled("loading")


def test_processing_suffix():
    assert processing_suffix(0) == ""
    assert processing_suffix(1) == " · processing 1 meeting"
    assert processing_suffix(2) == " · processing 2 meetings"


def test_quit_warning():
    assert quit_warning(False, 0) is None
    assert quit_warning(False, 1) == "1 meeting is still processing. Quitting discards it."
    assert quit_warning(False, 2) == "2 meetings are still processing. Quitting discards them."
    assert quit_warning(True, 0) == "The current recording will be discarded."
    assert quit_warning(True, 1) == "The current recording and 1 processing meeting will be discarded."
    assert quit_warning(True, 3) == "The current recording and 3 processing meetings will be discarded."
