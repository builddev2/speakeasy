from pathlib import Path

import pytest
from AppKit import NSApplication, NSAppearanceNameAqua, NSAppearanceNameDarkAqua

from speakeasy import settings
from speakeasy.ui import appearance

NSApplication.sharedApplication()


def test_setting_defaults_to_system_and_validates():
    assert settings.get_appearance() == "system"
    assert settings.set_appearance("light") == "light"
    assert settings.get_appearance() == "light"
    with pytest.raises(ValueError):
        settings.set_appearance("sepia")
    data = settings._read()
    data["appearance"] = 7
    settings._write(data)
    assert settings.get_appearance() == "system"


def test_apply_sets_the_app_appearance():
    app = NSApplication.sharedApplication()
    appearance.apply("dark")
    assert app.appearance().name() == NSAppearanceNameDarkAqua
    appearance.apply("light")
    assert app.appearance().name() == NSAppearanceNameAqua
    appearance.apply("system")
    assert app.appearance() is None


def test_web_windows_no_longer_force_dark():
    text = Path(__file__).resolve().parents[1].joinpath("speakeasy/ui/webwindow.py").read_text()
    assert "DarkAqua" not in text and "setAppearance_" not in text


def test_webwindow_init_applies_the_saved_appearance():
    # Constructing a real WebWindow needs a live WKWebView, so pin the call in
    # the source instead: __init__ must call appearance.apply_saved().
    import inspect
    import re

    from speakeasy.ui import webwindow

    src = inspect.getsource(webwindow.WebWindow.__init__)
    code = "\n".join(re.sub(r"#.*", "", line) for line in src.splitlines())
    assert "appearance.apply_saved()" in code
