import pytest

from speakeasy.ui.pill_model import (
    PillMachine, audio_indicator, clamp_origin, default_origin, drawer_rows,
    format_timer, pill_view)

OK = {"mic_first_buffer": True, "system_first_buffer": True, "system_nonzero_signal": True}


@pytest.mark.parametrize("s,text", [(0, "00:00"), (59.9, "00:59"), (723, "12:03"),
                                    (3600, "1:00:00"), (3723, "1:02:03")])
def test_format_timer(s, text):
    assert format_timer(s) == text


def test_indicator_ok_when_both_tracks_have_signal():
    assert audio_indicator(OK, "mic_and_system", 30) == "ok"


def test_indicator_waits_then_warns_without_signal():
    quiet = {**OK, "system_nonzero_signal": False}
    assert audio_indicator(quiet, "mic_and_system", 9.9) == "waiting"
    assert audio_indicator(quiet, "mic_and_system", 10) == "warning"
    assert audio_indicator({}, "mic_and_system", 2) == "waiting"
    assert audio_indicator({}, "mic_and_system", 12) == "warning"


@pytest.mark.parametrize("bad", [{"helper_exited": True, "helper_exit_reason": "crash"},
                                 {"system_writer_failed": True}, {"mic_writer_failed": True},
                                 {"mic_writer_lagged": True}, {"system_writer_lagged": True}])
def test_indicator_warns_on_failures(bad):
    assert audio_indicator({**OK, **bad}, "mic_and_system", 30) == "warning"


def test_requested_stop_is_not_a_failure():
    assert audio_indicator({**OK, "helper_exited": True, "helper_exit_reason": "requested_stop"},
                           "mic_and_system", 30) == "ok"


def test_mic_only_fallback_warns():
    assert audio_indicator({"mic_first_buffer": True}, "mic_only", 30) == "warning"


def test_drawer_rows_working():
    rows = drawer_rows(OK, "mic_and_system", "available", 30, "Weekly 1:1")
    assert [(r.label, r.value, r.tone) for r in rows] == [
        ("Event", "Weekly 1:1", "action"),
        ("Microphone", "You · working", "ok"),
        ("System audio", "All apps · working", "ok"),
        ("Dictation", "Paused until the meeting ends", "muted")]


def test_drawer_rows_unlinked_and_problems():
    rows = drawer_rows({"mic_first_buffer": True}, "mic_only", "permission_denied_or_unavailable", 30, None)
    assert rows[0].value == "Link to event…"
    assert rows[2].tone == "warning"
    assert "System Settings" in rows[2].value


def test_drawer_rows_never_mention_apps_or_pids():
    rows = drawer_rows({**OK, "capture_scope": "selected"}, "mic_and_system", "available", 30, None)
    assert all("PID" not in r.value for r in rows)


def m(*steps):
    machine = PillMachine()
    for step, *args in steps:
        getattr(machine, step)(*args)
    return machine


def test_recording_then_saved_then_expiry():
    machine = m(("on_state", "meeting_recording", None), ("on_state", "meeting_processing", None))
    assert machine.phase == "processing"
    machine.on_saved("m-1")
    machine.on_state("ready", None)
    assert (machine.phase, machine.saved_id) == ("saved", "m-1")
    machine.expire_saved()
    assert machine.phase == "hidden"


def test_cancel_confirm_keep_returns_to_processing():
    machine = m(("on_state", "meeting_processing", None), ("cancel",))
    assert machine.phase == "confirm_discard"
    machine.keep()
    assert machine.phase == "processing"


def test_discard_then_idle_hides():
    machine = m(("on_state", "meeting_processing", None), ("cancel",), ("discard",))
    assert machine.phase == "cancelling"
    machine.on_state("meeting_processing", None)
    assert machine.phase == "cancelling"
    machine.on_state("ready", None)
    assert machine.phase == "hidden"


def test_saved_during_cancel_shows_saved():
    machine = m(("on_state", "meeting_processing", None), ("cancel",), ("discard",), ("on_saved", "m-2"))
    machine.on_state("ready", None)
    assert (machine.phase, machine.saved_id) == ("saved", "m-2")


def test_processing_error_shows_failed_until_closed():
    machine = m(("on_state", "meeting_processing", None), ("on_state", "ready", "processing_failed"))
    assert machine.phase == "failed"
    machine.close()
    assert machine.phase == "hidden"


def test_cleanup_error_after_save_keeps_saved():
    machine = m(("on_state", "meeting_processing", None), ("on_saved", "m-3"),
                ("on_state", "ready", "cleanup_failed"))
    assert machine.phase == "saved"


def test_new_recording_replaces_saved():
    machine = m(("on_state", "meeting_processing", None), ("on_saved", "m-4"), ("on_state", "ready", None),
                ("on_state", "meeting_recording", None))
    assert (machine.phase, machine.saved_id) == ("recording", None)


def test_idle_without_meeting_stays_hidden():
    assert m(("on_state", "ready", None), ("on_state", "mic_failed", None)).phase == "hidden"


def test_pill_view_recording_and_processing():
    machine = m(("on_state", "meeting_recording", None))
    view = pill_view(machine, title=None, elapsed=723, health=OK, capture_mode="mic_and_system", progress=None)
    assert (view.title, view.detail, view.indicator, view.buttons) == (
        "Untitled meeting", "12:03", "ok", ("notes", "stop"))
    machine.on_state("meeting_processing", None)
    view = pill_view(machine, title="Weekly", elapsed=0, health={}, capture_mode="mic_only",
                     progress="Transcribing meeting… 42%")
    assert (view.title, view.detail, view.buttons) == ("Processing", "Transcribing meeting… 42%", ("notes", "cancel"))


def test_pill_view_other_phases():
    machine = m(("on_state", "meeting_processing", None), ("cancel",))
    assert pill_view(machine, title=None, elapsed=0, health={}, capture_mode="", progress=None).title == "Discard meeting?"
    machine.discard()
    assert pill_view(machine, title=None, elapsed=0, health={}, capture_mode="", progress=None).title == "Cancelling…"


def test_clamp_origin_accepts_fully_visible():
    assert clamp_origin((100, 800), (360, 36), [(0, 0, 1440, 875)]) == (100, 800)


def test_clamp_origin_rejects_offscreen():
    assert clamp_origin((2000, 800), (360, 36), [(0, 0, 1440, 875)]) is None
    assert clamp_origin((1200, 800), (360, 36), [(0, 0, 1440, 875)]) is None  # straddles the edge


def test_default_origin_is_top_centre():
    assert default_origin((0, 0, 1440, 875), (360, 36)) == (540.0, 831.0)
