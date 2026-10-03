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
