from pathlib import Path

from speakeasy.ui.window_sizes import SIZES

UI = Path(__file__).resolve().parents[1] / "speakeasy" / "ui"


def test_pinned_defaults_minimums_and_autosave_names():
    assert {k: (v.default, v.minimum, v.autosave) for k, v in SIZES.items()} == {
        "dock": ((360, 430), (340, 400), "SpeakeasyDockFrame"),
        "training": ((640, 440), (560, 400), "SpeakeasyTrainingFrame"),
        "diagnostic": ((660, 600), (560, 520), "SpeakeasyDiagnosticFrame"),
    }
    for v in SIZES.values():
        assert v.minimum[0] <= v.default[0] and v.minimum[1] <= v.default[1]


def test_every_web_window_is_resizable_and_remembers_its_frame():
    for module, key in (("main_window.py", "dock"), ("training_window.py", "training"),
                        ("diagnostic_window.py", "diagnostic"), ("meetings_window.py", None)):
        text = (UI / module).read_text()
        assert "resizable=True" in text, module
        assert "setFrameAutosaveName_" in text, module
        if key:
            assert f'SIZES["{key}"]' in text, module
