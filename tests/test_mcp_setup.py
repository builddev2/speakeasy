import json
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

from speakeasy import mcp_setup

NOW = datetime(2026, 9, 28, 18, 0, 0, tzinfo=timezone.utc)
SRC_ROOT = "/Users/x/Coding - General/Speakeasy"  # spaces on purpose


def test_frozen_installed_info():
    info = mcp_setup.setup_info(frozen=True, executable=mcp_setup.INSTALLED_EXECUTABLE,
                                repo_root=SRC_ROOT, last_used=None, now=NOW)
    assert info["command"] == ("claude mcp add speakeasy -- "
                               "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp")
    assert json.loads(info["desktopJson"]) == {"mcpServers": {"speakeasy": {
        "command": mcp_setup.INSTALLED_EXECUTABLE, "args": ["--mcp"]}}}
    assert info["executable"] == mcp_setup.INSTALLED_EXECUTABLE
    assert info["lastUsed"] is None


def test_source_info_quotes_paths_with_spaces_and_sets_pythonpath():
    exe = f"{SRC_ROOT}/.venv/bin/python"
    info = mcp_setup.setup_info(frozen=False, executable=exe, repo_root=SRC_ROOT,
                                last_used=None, now=NOW)
    parts = shlex.split(info["command"])
    assert parts[:3] == ["claude", "mcp", "add"]
    assert parts[parts.index("-e") + 1] == f"PYTHONPATH={SRC_ROOT}"
    assert parts[parts.index("--") + 1:] == [exe, "-m", "speakeasy", "--mcp"]
    cfg = json.loads(info["desktopJson"])["mcpServers"]["speakeasy"]
    assert cfg == {"command": exe, "args": ["-m", "speakeasy", "--mcp"],
                   "env": {"PYTHONPATH": SRC_ROOT}}
    assert info["extensionAvailable"] is False and "installed app" in info["extensionNote"]


def test_extension_needs_the_installed_bundle(tmp_path, monkeypatch):
    elsewhere = str(tmp_path / "dist/Speakeasy.app/Contents/MacOS/Speakeasy")
    info = mcp_setup.setup_info(frozen=True, executable=elsewhere, repo_root=SRC_ROOT,
                                last_used=None, now=NOW)
    assert info["extensionAvailable"] is False
    assert "Applications" in info["extensionNote"]


def test_extension_available_when_installed_and_bundled(tmp_path, monkeypatch):
    mcpb = tmp_path / "Speakeasy.mcpb"
    mcpb.write_bytes(b"zip")
    monkeypatch.setattr(mcp_setup, "mcpb_path", lambda frozen, exe: mcpb)
    info = mcp_setup.setup_info(frozen=True, executable=mcp_setup.INSTALLED_EXECUTABLE,
                                repo_root=SRC_ROOT, last_used=None, now=NOW)
    assert info["extensionAvailable"] is True and info["extensionNote"] is None


def test_extension_unavailable_when_installed_but_built_without_it(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_setup, "mcpb_path", lambda frozen, exe: tmp_path / "missing.mcpb")
    info = mcp_setup.setup_info(frozen=True, executable=mcp_setup.INSTALLED_EXECUTABLE,
                                repo_root=SRC_ROOT, last_used=None, now=NOW)
    assert info["extensionAvailable"] is False
    assert "without the extension" in info["extensionNote"]


def test_mcpb_path():
    assert mcp_setup.mcpb_path(False, "/x/python") is None
    assert mcp_setup.mcpb_path(True, mcp_setup.INSTALLED_EXECUTABLE) == Path(
        "/Applications/Speakeasy.app/Contents/Resources/Speakeasy.mcpb")


def test_last_used_label():
    L = mcp_setup.last_used_label
    assert L(None, NOW) is None
    assert L(NOW - timedelta(seconds=30), NOW) == "just now"
    assert L(NOW + timedelta(seconds=30), NOW) == "just now"  # clock skew
    assert L(NOW - timedelta(minutes=2), NOW) == "2 min ago"
    assert L(NOW - timedelta(minutes=59, seconds=59), NOW) == "59 min ago"
    assert L(NOW - timedelta(hours=3), NOW) == "3 h ago"
    assert L(NOW - timedelta(days=3), NOW) == "on " + (NOW - timedelta(days=3)).astimezone().strftime("%a %-d %b")
