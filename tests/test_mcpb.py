"""The Claude Desktop Extension stays in step with the server and the app."""
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from speakeasy import mcp_setup
from speakeasy.mcp_server import SERVER_VERSION
from speakeasy.mcp_tools import build_tools

REPO = Path(__file__).resolve().parents[1]
MCPB = REPO / "packaging" / "mcpb"
LAUNCHER = MCPB / "server" / "speakeasy-mcp"


def _manifest():
    return json.loads((MCPB / "manifest.json").read_text())


def test_manifest_tools_match_tools_list_exactly(library_path):
    from speakeasy.meeting_library import MeetingLibrary
    served = [(t.name, t.description) for t in build_tools(MeetingLibrary()).values()]
    assert [(t["name"], t["description"]) for t in _manifest()["tools"]] == served


def test_manifest_identity_and_version():
    m = _manifest()
    assert m["manifest_version"] == "0.3"
    assert m["name"] == "speakeasy" and m["display_name"] == "Speakeasy Meetings"
    assert m["compatibility"]["platforms"] == ["darwin"]
    spec = (REPO / "packaging" / "Speakeasy.spec").read_text()
    app_version = re.search(r'"CFBundleShortVersionString":\s*"([^"]+)"', spec).group(1)
    assert m["version"] == app_version == SERVER_VERSION


def test_manifest_runs_the_bundled_launcher():
    server = _manifest()["server"]
    assert server["type"] == "binary"
    assert server["entry_point"] == "server/speakeasy-mcp"
    assert server["mcp_config"]["command"] == "${__dirname}/server/speakeasy-mcp"
    assert (MCPB / server["entry_point"]).is_file()


def test_launcher_path_matches_setup_info():
    path = re.search(r'^APP_EXE="([^"]+)"$', LAUNCHER.read_text(), re.M).group(1)
    info = mcp_setup.setup_info(frozen=True, executable=mcp_setup.INSTALLED_EXECUTABLE,
                                repo_root="/r", last_used=None,
                                now=datetime.now())
    assert path == mcp_setup.INSTALLED_EXECUTABLE == info["executable"]


def test_launcher_is_executable():
    assert os.access(LAUNCHER, os.X_OK)


def _launcher_with(tmp_path, app_exe):
    script = tmp_path / "speakeasy-mcp"
    script.write_text(re.sub(r'^APP_EXE=".*"$', f'APP_EXE="{app_exe}"',
                             LAUNCHER.read_text(), flags=re.M))
    script.chmod(0o755)
    return script


def test_launcher_reports_a_missing_app_on_stderr(tmp_path):
    proc = subprocess.run([str(_launcher_with(tmp_path, tmp_path / "nope"))],
                          capture_output=True, timeout=10)
    assert proc.returncode == 1 and proc.stdout == b""
    assert b"Speakeasy" in proc.stderr and b"Applications" in proc.stderr


def test_launcher_execs_the_app_with_mcp(tmp_path):
    fake = tmp_path / "Speakeasy"
    fake.write_text('#!/bin/sh\necho "args:$*"\n')
    fake.chmod(0o755)
    proc = subprocess.run([str(_launcher_with(tmp_path, fake))],
                          capture_output=True, timeout=10)
    assert proc.returncode == 0 and proc.stdout == b"args:--mcp\n"
