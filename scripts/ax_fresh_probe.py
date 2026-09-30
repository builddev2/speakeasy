"""Probe how a freshly launched Electron/Chromium app answers AXFocusedUIElement.

Content-free: records only AX error codes, booleans, roles and timings; never
reads a value, title or selection.

  observe  reads only: settable checks, then polls the focused element.
  write    the same, but also writes --attr (default AXManualAccessibility) = True once,
           directly, regardless of what the settable check said.

The app must NOT be running; the probe launches it itself and does not touch
the keyboard or mouse. Don't touch anything until it prints "done".

  .venv/bin/python scripts/ax_fresh_probe.py --mode observe
  .venv/bin/python scripts/ax_fresh_probe.py --mode write
  .venv/bin/python scripts/ax_fresh_probe.py --mode observe --bundle com.microsoft.teams2
"""

import argparse
import json
import subprocess
import time

import ApplicationServices as ax
from AppKit import NSRunningApplication, NSWorkspace

ATTRS = ("AXManualAccessibility", "AXEnhancedUserInterface")


def ms(t0):
    return round((time.monotonic() - t0) * 1000, 1)


def emit(**fields):
    print(json.dumps(fields), flush=True)


def frontmost_bundle():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.bundleIdentifier()) if app is not None else None


def focused(owner):
    error, element = ax.AXUIElementCopyAttributeValue(owner, "AXFocusedUIElement", None)
    return int(error), element


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bundle", default="com.openai.codex")
    p.add_argument("--mode", choices=("observe", "write"), required=True)
    p.add_argument("--settle", type=float, default=4.0,
                   help="seconds after the process appears before the first AX query")
    p.add_argument("--duration", type=float, default=8.0,
                   help="seconds to keep polling AXFocusedUIElement")
    p.add_argument("--interval", type=float, default=0.02)
    p.add_argument("--attr", choices=ATTRS, default="AXManualAccessibility",
                   help="attribute written once in write mode")
    a = p.parse_args()

    trusted = bool(ax.AXIsProcessTrusted())
    emit(event="start", mode=a.mode, bundle=a.bundle, trusted=trusted,
         settle_s=a.settle, duration_s=a.duration, interval_s=a.interval)
    if not trusted:
        emit(event="abort", reason="this process lacks Accessibility permission")
        return
    if NSRunningApplication.runningApplicationsWithBundleIdentifier_(a.bundle):
        emit(event="abort", reason="app already running; quit it first (fresh launch needed)")
        return

    t_launch = time.monotonic()
    subprocess.run(["open", "-b", a.bundle], check=True)
    pid = None
    while pid is None and time.monotonic() - t_launch < 30:
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(a.bundle)
        if apps:
            pid = apps[0].processIdentifier()
        else:
            time.sleep(0.01)
    emit(event="process", pid=pid, ms_since_launch=ms(t_launch))
    if pid is None:
        return

    # No AX traffic during the settle: the queries themselves may be the trigger.
    time.sleep(a.settle)
    owner = ax.AXUIElementCreateApplication(pid)
    ax.AXUIElementSetMessagingTimeout(owner, .1)

    t0 = time.monotonic()
    # Same order as speakeasy.injector.focused_target: one query first.
    err, element = focused(owner)
    emit(event="first_query", ms_since_launch=ms(t_launch), ax_error=err,
         answered=element is not None, frontmost=frontmost_bundle())

    for name in ATTRS:
        s_err, settable = ax.AXUIElementIsAttributeSettable(owner, name, None)
        r_err, value = ax.AXUIElementCopyAttributeValue(owner, name, None)
        emit(event="attribute", name=name, settable_error=int(s_err),
             settable=bool(settable), read_error=int(r_err),
             read_value=(bool(value) if isinstance(value, (bool, int)) else
                         None if value is None else type(value).__name__))
    n_err, names = ax.AXUIElementCopyAttributeNames(owner, None)
    emit(event="attribute_names", error=int(n_err),
         **{f"lists_{n}": (n in names) if names else None for n in ATTRS})

    if a.mode == "write":
        t_set = time.monotonic()
        w_err = ax.AXUIElementSetAttributeValue(owner, a.attr, True)
        set_ms = ms(t_set)
        r_err, value = ax.AXUIElementCopyAttributeValue(owner, a.attr, None)
        emit(event="write", name=a.attr, ms=ms(t0), set_call_ms=set_ms,
             set_error=int(w_err), read_back_error=int(r_err),
             read_back=bool(value) if isinstance(value, (bool, int)) else None)

    last = err
    queries = 1
    answered_ms = 0.0 if element is not None else None
    while answered_ms is None and time.monotonic() - t0 < a.duration:
        time.sleep(a.interval)
        err, element = focused(owner)
        queries += 1
        if err != last:
            emit(event="transition", ms=ms(t0), ax_error=err, queries=queries)
            last = err
        if err == 0 and element is not None:
            answered_ms = ms(t0)

    role = None
    if element is not None:
        ax.AXUIElementSetMessagingTimeout(element, .1)
        r_err, role = ax.AXUIElementCopyAttributeValue(element, "AXRole", None)
        role = str(role) if r_err == 0 and role is not None else f"error {int(r_err)}"
    # Distinguish "no tree yet" from "tree but nothing focused".
    w_err, window = ax.AXUIElementCopyAttributeValue(owner, "AXFocusedWindow", None)
    children = None
    if window is not None:
        c_err, kids = ax.AXUIElementCopyAttributeValue(window, "AXChildren", None)
        children = len(kids) if c_err == 0 and kids is not None else f"error {int(c_err)}"
    emit(event="done", focused_answered_ms=answered_ms, queries=queries,
         last_ax_error=err, focused_role=role, focused_window_error=int(w_err),
         focused_window_children=children, frontmost=frontmost_bundle())


if __name__ == "__main__":
    main()
