import sys
from types import SimpleNamespace

from speakeasy.ui import pill_controller as pc


def test_mouse_screen_falls_back_when_no_screens(monkeypatch):
    fake = SimpleNamespace(
        NSEvent=SimpleNamespace(mouseLocation=lambda: (0, 0)),
        NSMouseInRect=lambda *a: False,
        NSScreen=SimpleNamespace(screens=lambda: []),
    )
    monkeypatch.setitem(sys.modules, "AppKit", fake)
    assert pc._mouse_screen() == pc.DEFAULT_FRAME
