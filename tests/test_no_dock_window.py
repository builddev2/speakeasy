from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dock_window_is_gone():
    assert not (ROOT / "speakeasy/ui/main_window.py").exists()
    assert not (ROOT / "frontend/src/dock").exists()
    assert not (ROOT / "frontend/dock.html").exists()


def test_nothing_references_the_dock_window():
    for path in [*ROOT.glob("speakeasy/**/*.py"), *ROOT.glob("scripts/*"), ROOT / "packaging/Speakeasy.spec",
                 ROOT / "frontend/vite.config.ts", ROOT / "frontend/index.html"]:
        text = path.read_text(errors="ignore")
        assert "main_window" not in text and "dock.html" not in text, path


def test_dock_click_always_opens_meetings():
    from types import SimpleNamespace
    from speakeasy.ui.menubar import AppDelegate

    opened = []
    delegate = AppDelegate.alloc().initWithProfileName_(None)
    delegate.controller = SimpleNamespace(open_meetings=lambda view: opened.append(view))
    assert delegate.applicationShouldHandleReopen_hasVisibleWindows_(None, True) is True
    assert opened == [None]
    opened.clear()
    assert delegate.applicationShouldHandleReopen_hasVisibleWindows_(None, False) is True
    assert opened == [None]


def test_launch_opens_meetings():
    source = (ROOT / "speakeasy/ui/menubar.py").read_text()
    launch = source.split("def applicationDidFinishLaunching_", 1)[1].split("def applicationWillTerminate_", 1)[0]
    assert "self.controller.open_meetings(None)" in launch


def test_frozen_app_hidden_imports_include_pill_modules():
    spec = (ROOT / "packaging/Speakeasy.spec").read_text()
    for name in ("pill_controller", "pill_panel", "next_up_view", "login_item"):
        assert f'"speakeasy.ui.{name}"' in spec
