"""resume() must replay the key edge dropped while the tap was deaf.

The paste path disables the tap while ⌘V is synthesized; a release that lands
in that window is never delivered. Dropping it strands an in-progress recording
(mic stuck open), so resume() reconciles against the key's real state.
"""

import Quartz

from speakeasy.hotkey import HotkeyListener


def _listener(monkeypatch, key_down):
    calls = []
    h = HotkeyListener(
        lambda: calls.append("start"),
        lambda: calls.append("end"),
    )
    h._tap = object()  # pretend a live tap exists without a real event tap
    monkeypatch.setattr(Quartz, "CGEventTapEnable", lambda tap, on: None)
    monkeypatch.setattr(h, "_key_is_down", lambda: key_down)
    return h, calls


def test_resume_replays_missed_release(monkeypatch):
    # Recording was in flight (_held) and the key came up during the paste.
    h, calls = _listener(monkeypatch, key_down=False)
    h._held = True

    h.resume()

    assert calls == ["end"]   # the dropped release is delivered → recording stops
    assert h._held is False


def test_resume_replays_missed_press(monkeypatch):
    h, calls = _listener(monkeypatch, key_down=True)
    h._held = False

    h.resume()

    assert calls == ["start"]
    assert h._held is True


def test_resume_no_edge_when_state_matches(monkeypatch):
    h, calls = _listener(monkeypatch, key_down=False)
    h._held = False

    h.resume()

    assert calls == []
    assert h._held is False


def test_resume_holds_through_when_key_still_down(monkeypatch):
    # User kept holding across the paste: no edge, stay armed for the real up.
    h, calls = _listener(monkeypatch, key_down=True)
    h._held = True

    h.resume()

    assert calls == []
    assert h._held is True
