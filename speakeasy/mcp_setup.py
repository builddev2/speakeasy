"""What the Connect Claude sheet shows: the exact commands for this copy of
Speakeasy, whether the one-click extension can be used, and when Claude
last called the MCP server. Pure (no AppKit) so it is unit tested.

Speakeasy never edits another app's settings: it only shows commands and
opens the bundled .mcpb so Claude Desktop runs its own install dialog.
"""

import json
import shlex
import sys
from datetime import datetime, timedelta
from pathlib import Path

INSTALLED_EXECUTABLE = "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy"
_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def server_argv(frozen: bool, executable: str) -> list[str]:
    return [executable, "--mcp"] if frozen else [executable, "-m", "speakeasy", "--mcp"]


def mcpb_path(frozen: bool, executable: str) -> Path | None:
    if not frozen:
        return None
    # <bundle>/Contents/MacOS/Speakeasy -> <bundle>/Contents/Resources/
    return Path(executable).parent.parent / "Resources" / "Speakeasy.mcpb"


def current_mcpb_path() -> Path | None:
    return mcpb_path(bool(getattr(sys, "frozen", False)), sys.executable)


def last_used_label(last_used: datetime | None, now: datetime) -> str | None:
    if last_used is None:
        return None
    age = now - last_used
    if age < timedelta(minutes=1):
        return "just now"
    if age < timedelta(hours=1):
        return f"{int(age.total_seconds() // 60)} min ago"
    if age < timedelta(days=1):
        return f"{int(age.total_seconds() // 3600)} h ago"
    return "on " + last_used.astimezone().strftime("%a %-d %b")


def setup_info(*, frozen: bool, executable: str, repo_root: str,
               last_used: datetime | None, now: datetime) -> dict:
    argv = server_argv(frozen, executable)
    env = {} if frozen else {"PYTHONPATH": repo_root}
    command = ["claude", "mcp", "add", "speakeasy"]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    server = {"command": argv[0], "args": argv[1:]}
    if env:
        server["env"] = env
    bundle = mcpb_path(frozen, executable)
    if not frozen:
        note = "The one-click extension is available in the installed app."
    elif executable != INSTALLED_EXECUTABLE:
        note = "Move Speakeasy to Applications to use the one-click extension."
    elif bundle is None or not bundle.is_file():
        note = "This copy of Speakeasy was built without the extension."
    else:
        note = None
    return {
        "command": shlex.join(command) + " -- " + shlex.join(argv),
        "desktopJson": json.dumps({"mcpServers": {"speakeasy": server}}, indent=2),
        "executable": argv[0],
        "lastUsed": last_used_label(last_used, now),
        "extensionAvailable": note is None,
        "extensionNote": note,
    }


def current_setup_info(now: datetime) -> dict:
    from .mcp_tools import read_last_used
    return setup_info(frozen=bool(getattr(sys, "frozen", False)), executable=sys.executable,
                      repo_root=_REPO_ROOT, last_used=read_last_used(), now=now)
