"""First-run TCC permission checks and guidance.

Speakeasy needs three grants: Microphone (macOS prompts automatically on the
first recording), Accessibility (the synthesized ⌘V paste), and Input
Monitoring (the global hotkey's event tap). The last two can't be prompted
into existence — the user must flip a switch in System Settings — so this
module detects what's missing and walks them there.
"""

from AppKit import NSAlert, NSAlertFirstButtonReturn, NSAlertSecondButtonReturn, NSWorkspace
from ApplicationServices import AXIsProcessTrusted
from Foundation import NSURL
from Quartz import CGPreflightListenEventAccess, CGRequestListenEventAccess

_ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
_INPUT_MONITORING_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
)


def accessibility_granted() -> bool:
    return bool(AXIsProcessTrusted())


def input_monitoring_granted() -> bool:
    return bool(CGPreflightListenEventAccess())


def all_granted() -> bool:
    return accessibility_granted() and input_monitoring_granted()


def _open_pane(url: str) -> None:
    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))


def show_guidance() -> None:
    """One alert explaining the missing grants, with buttons to the panes.

    Also fires the system's own Input Monitoring prompt (adds Speakeasy to
    the pane's list so the user only has to flip the switch). Call on the
    main thread. SPEAKEASY_SKIP_PERMISSION_PROMPT=1 suppresses it (used by
    automated smoke tests so no dialogs pop mid-run).
    """
    import os

    if os.environ.get("SPEAKEASY_SKIP_PERMISSION_PROMPT"):
        return
    missing_input = not input_monitoring_granted()
    missing_ax = not accessibility_granted()
    if missing_input:
        CGRequestListenEventAccess()

    lines = ["Speakeasy needs these permissions to work everywhere:"]
    panes = []
    if missing_input:
        lines.append("• Input Monitoring — to notice the Right ⌘ hotkey")
        panes.append(("Open Input Monitoring", _INPUT_MONITORING_PANE))
    if missing_ax:
        lines.append("• Accessibility — to paste the transcribed text (⌘V)")
        panes.append(("Open Accessibility", _ACCESSIBILITY_PANE))
    lines.append(
        "\nEnable Speakeasy in System Settings → Privacy & Security, "
        "then relaunch the app."
    )

    alert = NSAlert.alloc().init()
    alert.setMessageText_("Allow Speakeasy to type for you")
    alert.setInformativeText_("\n".join(lines))
    for title, _ in panes:
        alert.addButtonWithTitle_(title)
    alert.addButtonWithTitle_("Later")

    response = alert.runModal()
    index = {
        NSAlertFirstButtonReturn: 0,
        NSAlertSecondButtonReturn: 1,
    }.get(response)
    if index is not None and index < len(panes):
        _open_pane(panes[index][1])
