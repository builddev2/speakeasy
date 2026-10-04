import pytest

from speakeasy.ui.menu_model import menu_flags


@pytest.mark.parametrize("state,flags", [
    ("ready", dict(next_up=True, meeting_notes=False, retry_mic=False, cancel=False)),
    ("meeting_recording", dict(next_up=False, meeting_notes=True, retry_mic=False, cancel=False)),
    ("meeting_processing", dict(next_up=False, meeting_notes=True, retry_mic=False, cancel=True)),
    ("mic_failed", dict(next_up=False, meeting_notes=False, retry_mic=True, cancel=False)),
    ("loading", dict(next_up=False, meeting_notes=False, retry_mic=False, cancel=False)),
])
def test_menu_flags(state, flags):
    assert menu_flags(state) == flags
