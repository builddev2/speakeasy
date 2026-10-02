# Record Prompts (Calendar + Call Detection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offer to record every meeting at the moment it starts: a calendar meeting starting, or another app starting to use the microphone. When a recorded call ends, ask whether to stop. Recording still starts and stops only on a click.

**Architecture:**
- The bundled Swift helper gains `--list-input-pids`.
- `call_detect.py` reduces its answer to one boolean on a `call-probe` thread.
- A pure `record_prompt.py` coordinator decides what the banner shows from calendar ticks, call observations, engine states and clicks.
- A thin main-thread `RecordPromptController` feeds the coordinator. It draws a native non-activating `RecordPromptPanel` and turns clicks into `begin_meeting` / `end_meeting` calls.

**Tech Stack:** Python 3 + PyObjC (AppKit/Foundation), Swift (Core Audio helper), React/TypeScript (Meetings settings sheet), pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-record-prompts-design.md`

## Execution roles (user requirement, 1 Oct 2026)

- **Session model: Opus 5.5.** Set it with `/model` before pasting the start prompt. The session plans, dispatches, reads the short reports and keeps this file current.
- **Implementers: Sonnet 5.5.** Use `Agent(model: "sonnet", subagent_type: "general-purpose")`, one per task. The brief contains:
  - the task text verbatim, plus Global Constraints and Review Focus;
  - the rule "full suite: Bash timeout 300000 ms; it runs ~90 s";
  - "report: verdict, files changed, test result; nothing else".
- **Reviewers: Opus 5.5.** Use `Agent(model: "opus", subagent_type: "general-purpose")`, after each task and once for the whole branch at the end. Each reviewer:
  - checks the task against the spec;
  - **verifies by mutation**: applies each listed mutation, confirms that a test fails, then restores the code;
  - runs mutations with `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider <file>`, then `find speakeasy tests -name __pycache__ -type d -prune -exec rm -rf {} +` after restoring;
  - reports a short verdict.
- **No subagent runs app code against the real `~/Library/Application Support/Speakeasy`.** Tests are HOME-isolated by `tests/conftest.py`. Any live run uses a temp HOME (`subprocess.run(..., env={"HOME": tmp})`). After any run, check that the folder is unchanged.

## Setup (once, before Task 1)

- [x] Create a worktree and branch `record-prompts` (superpowers:using-git-worktrees or `EnterWorktree`), then `git merge --ff-only master`.
- [x] Symlink the git-ignored dirs from the main checkout: `.venv`, `frontend/node_modules` (check that `.bin/tsc` exists first) and `models`. Never run `ln -sf` onto an existing directory symlink.
- [x] Baseline: `.venv/bin/python -m pytest -q -p no:cacheprovider` (timeout 300000). Expect **929 passed** (measured 1 Oct 2026, 82 s).

## Global Constraints

- Fully offline: no network calls, no new Python or npm dependency. Dependencies stay pinned.
- Speakeasy never starts or stops recording by itself. Every `begin_meeting` / `end_meeting` added here comes from a banner click.
- EventKit is used only on the `calendar` thread. The banner reads the cached `calendar_events` table through `MeetingLibrary`.
- PIDs and app names from call detection never leave `call_detect.py`. Only a `bool | None` crosses the module boundary. Never log, store or show them.
- The capture-health schema is unchanged.
- The probe thread never opens an audio stream and never touches the model, the `worker`/`control` executors, or UI objects. It hands results to the main thread with `performSelectorOnMainThread`.
- UI objects are main-thread only. PyObjC: decorate non-selector methods with `@objc.python_method`, use `objc.super(Cls, self).init()`, and define each ObjC class name once.
- The banner never takes keyboard focus: borderless `NSPanel` with `NSWindowStyleMaskNonactivatingPanel`, `orderFrontRegardless()`, never `makeKeyAndOrderFront_` or `activateIgnoringOtherApps_`.
- Exact values:
  - calendar check every **30 s**;
  - prompt window **start − 2 min … start + 5 min** (existing `calendar_match`);
  - offer timeout **5 min**;
  - call offer after **10 s** of other-app mic use;
  - call over after **60 s** without it;
  - probe every **5 s**;
  - helper call timeout **1.0 s**;
  - "Recording" confirmation **2 s**;
  - banner **360 × 92 pt**, **12 pt** from the top-right of the visible screen.
- Settings copy:
  - "Offer to record calendar meetings" (existing);
  - "Offer to record calls in other apps" (new, default on).
- Banner copy:
  - buttons "Record" / "Not Now" and "Stop" / "Keep Recording";
  - title "Call ended" with subtitle "Stop recording?";
  - "Record this call?" with subtitle "Another app is using the microphone";
  - "N more" menu items "Record ‘<title>’".
- Do not touch dictation, focus or insertion code (`injector.py`, `hotkey.py`, `ax_warmup.py`, `recorder.py`, `microphone_helper.py`).
- Main branch is `master`. Commit per task; do not push until the user says to merge.

## Review Focus

1. **A muted Teams/Zoom call that releases the mic for > 60 s** shows "Call ended" while you are still in the call. It must only ask, and mic use resuming must hide it. Test: `test_mic_resuming_hides_call_ended` (Task 3).
2. **Overlapping events** (real 2 Oct 08:00: VFA Leads Sync Up, Pay Stub Review, 1st Hday, Planning Services Weekly Tactical). Only events with other people are offered, nearest first, and the rest go in "N more". Test: `test_calendar_offer_needs_people_and_prefers_nearest` (Task 3).
3. **The user clicks Begin Meeting by hand while a banner shows.** The banner closes, there is no second `begin_meeting`, and those events are not offered again. Test: `test_starting_a_meeting_closes_the_offer` (Task 3).
4. **Speakeasy's own mic use is not "another app".** This covers the in-process meeting recorder, the dictation helper and the system-audio helper. Tests: `test_other_app_using_mic_ignores_speakeasy_processes` and `test_own_process_ids_include_children` (Task 1); `test_not_ready_never_offers_call` (Task 3).
5. **Relaunch mid-day.** A dismissed event is not offered again; the next day starts empty. Test: `test_prompted_events_are_kept_for_the_day_only` (Task 2).

---

### Task 1: Detect another app using the microphone

**Files:**
- Modify: `native/SystemAudioCapture.swift` (`eligibleAudioProcessIDs` ~L194–232, `main` ~L582)
- Modify: `speakeasy/system_audio.py:82-108` (`eligible_process_ids`)
- Create: `speakeasy/call_detect.py`
- Test: `tests/test_system_audio.py`, `tests/test_call_detect.py`

**Interfaces:**
- Produces:
  - `system_audio.input_process_ids() -> set[int] | None`, where `None` means "can't tell";
  - `call_detect.own_process_ids() -> set[int]`;
  - `call_detect.other_app_using_mic(input_ids=None, own_ids=None) -> bool | None`;
  - `call_detect.CallProbe(on_result: Callable[[bool], None], wanted: Callable[[], bool], probe=None, interval=POLL_SECONDS)` with `.start()` and `.stop()`;
  - `call_detect.POLL_SECONDS = 5.0`.

- [x] **Step 1: Swift — split the process listing and add `--list-input-pids`**

Replace `eligibleAudioProcessIDs()` with these four functions. The body of `audioProcessObjects` is the existing list code, moved.

```swift
private func audioProcessObjects() throws -> [AudioObjectID] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyProcessObjectList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    try check(
        AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size
        ),
        "process_list_size"
    )
    var objects = [AudioObjectID](
        repeating: kAudioObjectUnknown,
        count: Int(size) / MemoryLayout<AudioObjectID>.size
    )
    guard !objects.isEmpty else { return [] }
    let status = objects.withUnsafeMutableBufferPointer { buffer in
        AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            0,
            nil,
            &size,
            buffer.baseAddress!
        )
    }
    try check(status, "process_list")
    return objects
}

private func processID(_ objectID: AudioObjectID) -> pid_t? {
    var pidAddress = AudioObjectPropertyAddress(
        mSelector: kAudioProcessPropertyPID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var processID: pid_t = 0
    var pidSize = UInt32(MemoryLayout<pid_t>.size)
    let pidStatus = AudioObjectGetPropertyData(
        objectID, &pidAddress, 0, nil, &pidSize, &processID
    )
    return pidStatus == noErr && processID > 0 ? processID : nil
}

// True while the process has Core Audio input running (the mic is in use).
private func isRunningInput(_ objectID: AudioObjectID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioProcessPropertyIsRunningInput,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var running: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    return AudioObjectGetPropertyData(objectID, &address, 0, nil, &size, &running) == noErr
        && running != 0
}

private func eligibleAudioProcessIDs() throws -> [pid_t] {
    try audioProcessObjects().compactMap(processID)
}

private func inputProcessIDs() throws -> [pid_t] {
    try audioProcessObjects().filter(isRunningInput).compactMap(processID)
}
```

In `SystemAudioCaptureMain.main()`, directly after the `--list-pids` block, add:

```swift
        if CommandLine.arguments.count == 2,
           CommandLine.arguments[1] == "--list-input-pids" {
            do {
                emit(["event": "input_processes", "pids": try inputProcessIDs()])
                exit(0)
            } catch {
                emit(["event": "error", "reason": "input_list_failed"])
                exit(5)
            }
        }
```

- [x] **Step 2: Build the helper and smoke-run it**

Run: `scripts/build_system_audio_helper.sh`, then `build/native/SpeakeasySystemAudioCapture --list-input-pids`.
Expected: one line `{"event":"input_processes","pids":[...]}`. The list is empty when nothing is recording. `--list-pids` still prints `eligible_processes`.

- [x] **Step 3: Write the failing Python tests**

Append to `tests/test_system_audio.py`:

```python
def _helper_prints(monkeypatch, payload):
    result = type("Result", (), {"stdout": json.dumps(payload)})()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return result

    monkeypatch.setattr(system_audio.subprocess, "run", run)
    return calls


def test_input_process_ids_are_read_from_helper(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    calls = _helper_prints(
        monkeypatch, {"event": "input_processes", "pids": [101, 0, -4, "7", 202]})
    assert system_audio.input_process_ids() == {101, 202}
    assert calls[0][1:] == ["--list-input-pids"]


def test_input_process_ids_empty_list_is_an_answer(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    _helper_prints(monkeypatch, {"event": "input_processes", "pids": []})
    assert system_audio.input_process_ids() == set()


def test_input_process_ids_unknown_when_helper_cannot_say(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    _helper_prints(monkeypatch, {"event": "error", "reason": "input_list_failed"})
    assert system_audio.input_process_ids() is None

    def timeout(*a, **k):
        raise system_audio.subprocess.TimeoutExpired("helper", 1.0)

    monkeypatch.setattr(system_audio.subprocess, "run", timeout)
    assert system_audio.input_process_ids() is None


def test_input_process_ids_unknown_before_macos_14_2(monkeypatch):
    monkeypatch.setattr(system_audio.platform, "mac_ver", lambda: ("14.1", (), ""))
    assert system_audio.input_process_ids() is None
```

Create `tests/test_call_detect.py`:

```python
import os
import threading

from speakeasy import call_detect


def test_other_app_using_mic_ignores_speakeasy_processes():
    assert call_detect.other_app_using_mic(
        input_ids=lambda: {10, 11}, own_ids=lambda: {10, 11}) is False
    assert call_detect.other_app_using_mic(
        input_ids=lambda: {10, 12}, own_ids=lambda: {10, 11}) is True
    assert call_detect.other_app_using_mic(
        input_ids=lambda: set(), own_ids=lambda: {10}) is False
    assert call_detect.other_app_using_mic(
        input_ids=lambda: None, own_ids=lambda: {10}) is None


def test_own_process_ids_include_children(monkeypatch):
    result = type("R", (), {"stdout": "201\n202\nnot-a-pid\n"})()
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return result

    monkeypatch.setattr(call_detect.subprocess, "run", run)
    assert call_detect.own_process_ids() == {os.getpid(), 201, 202}
    assert seen[0] == ["/usr/bin/pgrep", "-P", str(os.getpid())]


def test_own_process_ids_survive_pgrep_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no pgrep")

    monkeypatch.setattr(call_detect.subprocess, "run", boom)
    assert call_detect.own_process_ids() == {os.getpid()}


def test_probe_reports_only_while_wanted_and_stops():
    wanted = threading.Event()
    got = threading.Event()
    calls, results = [], []

    def probe():
        calls.append(1)
        return True

    def on_result(value):
        results.append(value)
        got.set()

    p = call_detect.CallProbe(on_result=on_result, wanted=wanted.is_set,
                              probe=probe, interval=0.01)
    p.start()
    assert not got.wait(0.1)
    assert calls == []
    wanted.set()
    assert got.wait(1.0)
    assert results[0] is True
    p.stop()
    p._thread.join(1.0)
    assert not p._thread.is_alive()


def test_probe_never_reports_unknown():
    calls = []

    def probe():
        calls.append(1)
        return None

    results = []
    p = call_detect.CallProbe(on_result=results.append, wanted=lambda: True,
                              probe=probe, interval=0.01)
    p.start()
    threading.Event().wait(0.1)
    p.stop()
    p._thread.join(1.0)
    assert calls and results == []
```

- [x] **Step 4: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_system_audio.py tests/test_call_detect.py`
Expected: FAIL (`input_process_ids` and `call_detect` do not exist).

- [x] **Step 5: Implement `system_audio.input_process_ids`, sharing parsing with `eligible_process_ids`**

Replace `eligible_process_ids` in `speakeasy/system_audio.py` with:

```python
def _helper_pids(flag: str, event_name: str) -> set[int] | None:
    """PIDs from one helper listing, or None when the helper can't say."""
    if capability() != "available":
        return None
    try:
        result = subprocess.run(
            [str(helper_path()), flag],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("event") == event_name:
            values = event.get("pids")
            if isinstance(values, list):
                return {
                    value
                    for value in values
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0
                }
    return None


def eligible_process_ids() -> set[int]:
    return _helper_pids("--list-pids", "eligible_processes") or set()


def input_process_ids() -> set[int] | None:
    """PIDs whose Core Audio input is running now (the mic is in use), or
    None when unknown. Transient: call_detect reduces it to one boolean and
    nothing logs or stores a PID."""
    return _helper_pids("--list-input-pids", "input_processes")
```

- [x] **Step 6: Create `speakeasy/call_detect.py`**

```python
"""Is another app using the microphone? One boolean for the record banner.

PIDs stay inside this module: they are never logged, stored or shown. The
probe runs on its own daemon thread because each answer costs a helper
process launch (up to 1 s); it never opens an audio stream and never
touches the model, the executors or UI objects.
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Callable

from . import system_audio

POLL_SECONDS = 5.0


def own_process_ids() -> set[int]:
    """Speakeasy and its direct children (dictation helper, system-audio
    helper, diarization child): their mic use is ours, not another app's."""
    own = os.getpid()
    ids = {own}
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-P", str(own)],
            capture_output=True, text=True, timeout=1.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ids
    ids.update(int(token) for token in result.stdout.split() if token.isdigit())
    return ids


def other_app_using_mic(input_ids=None, own_ids=None) -> bool | None:
    active = (input_ids or system_audio.input_process_ids)()
    if active is None:
        return None
    return bool(active - (own_ids or own_process_ids)())


class CallProbe:
    """Every `interval` seconds while `wanted()` is true, ask `probe` and
    hand a known answer to `on_result`, which must hop to the main thread
    itself. `wanted` is read from this thread: keep it a plain flag read."""

    def __init__(self, on_result: Callable[[bool], None], wanted: Callable[[], bool],
                 probe: Callable[[], bool | None] | None = None,
                 interval: float = POLL_SECONDS) -> None:
        self._on_result = on_result
        self._wanted = wanted
        self._probe = probe or other_app_using_mic
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="call-probe", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        # No join: at most one helper run (1 s timeout) is in flight, and the
        # thread is a daemon, so quit never waits on it.
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if not self._wanted():
                continue
            result = self._probe()
            if result is not None and not self._stop.is_set():
                self._on_result(result)
```

- [x] **Step 7: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_system_audio.py tests/test_call_detect.py`, expect PASS.
Then run the full suite with a 300000 ms timeout and expect all tests to pass.

- [x] **Step 8: Commit**

```bash
git add native/SystemAudioCapture.swift speakeasy/system_audio.py speakeasy/call_detect.py tests/test_system_audio.py tests/test_call_detect.py
git commit -m "Call detect: helper lists PIDs with mic input running; one-boolean probe"
```

**Review mutations (Opus):**
- remove `- (own_ids or own_process_ids)()`;
- return `set()` instead of `None` on timeout;
- drop the `value > 0` filter;
- call `on_result` when `result is None`;
- skip the `wanted()` check.

---

### Task 2: Settings — call-detection toggle and per-day prompted events

**Files:**
- Modify: `speakeasy/settings.py:120-152`
- Modify: `speakeasy/ui/meetings_bridge.py:209-222`
- Modify: `frontend/src/mock/meetings.ts` (`MeetingSettings` ~L35, mock ~L627), `frontend/src/meetings/SettingsSheet.tsx`, `frontend/src/meetings/App.tsx:~324-340`
- Delete: `frontend/prompt.html`, `frontend/src/prompt/` (the unused web mock, replaced by the native panel); remove the `prompt:` input in `frontend/vite.config.ts:15`
- Test: `tests/test_settings.py`, `tests/test_meetings_bridge.py`

**Interfaces:**
- Produces:
  - `settings.get_meeting_settings() -> {"offer_to_record": bool, "detect_calls": bool, "calendar_choices": dict}`;
  - `settings.set_meeting_settings(*, offer_to_record=None, detect_calls=None, calendar_choices=None) -> dict`;
  - `settings.get_prompted_events(day: str) -> set[str]`;
  - `settings.set_prompted_events(day: str, keys) -> None`;
  - bridge payload key `detectCalls`.

- [x] **Step 1: Update the existing settings tests and add new ones (failing)**

In `tests/test_settings.py`:
- Add `"detect_calls": True` to the expected dicts in `test_meeting_settings_defaults_and_round_trip` (both asserts) and `test_meeting_settings_ignore_corrupt_file`.
- Add `settings.set_meeting_settings(detect_calls="no")` to the `pytest.raises(ValueError)` checks.
- Append:

```python
def test_detect_calls_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.get_meeting_settings()["detect_calls"] is True
    settings.set_meeting_settings(detect_calls=False)
    assert settings.get_meeting_settings()["detect_calls"] is False
    assert settings.get_meeting_settings()["offer_to_record"] is True


def test_prompted_events_are_kept_for_the_day_only(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.get_prompted_events("2026-10-02") == set()
    settings.set_prompted_events("2026-10-02", {"a@2026-10-02T15:00:00Z"})
    assert settings.get_prompted_events("2026-10-02") == {"a@2026-10-02T15:00:00Z"}
    assert settings.get_prompted_events("2026-10-03") == set()
    assert settings.get_meeting_settings()["offer_to_record"] is True


def test_prompted_events_keep_the_latest_500(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    keys = {f"e{i}@2026-10-02T{i // 60:02d}:{i % 60:02d}:00Z" for i in range(600)}
    settings.set_prompted_events("2026-10-02", keys)
    kept = settings.get_prompted_events("2026-10-02")
    assert len(kept) == 500
    assert "e599@2026-10-02T09:59:00Z" in kept and "e0@2026-10-02T00:00:00Z" not in kept


def test_prompted_events_ignore_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(
        '{"record_prompted": {"day": "2026-10-02", "keys": ["a", 3, null]}}')
    assert settings.get_prompted_events("2026-10-02") == {"a"}
```

In `tests/test_meetings_bridge.py`, `test_meeting_settings_round_trip`, add:

```python
    out = b.settings_set_payload({"detectCalls": False})
    assert out["detectCalls"] is False
    assert settings.get_meeting_settings()["detect_calls"] is False
```

- [x] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_settings.py tests/test_meetings_bridge.py`
Expected: FAIL (`detect_calls` and `get_prompted_events` are missing).

- [x] **Step 3: Implement in `speakeasy/settings.py`**

Replace `get_meeting_settings` and `set_meeting_settings`, and add the two prompted-event functions after them:

```python
def get_meeting_settings() -> dict:
    """Record-offer toggles and per-calendar include choices. A calendar
    missing from calendar_choices uses its default (calendar_sync)."""
    raw = _read().get("meetings")
    raw = raw if isinstance(raw, dict) else {}
    offer = raw.get("offer_to_record")
    detect = raw.get("detect_calls")
    choices = raw.get("calendar_choices")
    return {
        "offer_to_record": offer if isinstance(offer, bool) else True,
        "detect_calls": detect if isinstance(detect, bool) else True,
        "calendar_choices": {
            k: v for k, v in choices.items() if isinstance(k, str) and isinstance(v, bool)
        } if isinstance(choices, dict) else {},
    }


def set_meeting_settings(*, offer_to_record=None, detect_calls=None,
                         calendar_choices=None) -> dict:
    current = get_meeting_settings()
    if offer_to_record is not None:
        if not isinstance(offer_to_record, bool):
            raise ValueError("Offer to record must be on or off.")
        current["offer_to_record"] = offer_to_record
    if detect_calls is not None:
        if not isinstance(detect_calls, bool):
            raise ValueError("Call detection must be on or off.")
        current["detect_calls"] = detect_calls
    if calendar_choices is not None:
        if (not isinstance(calendar_choices, dict) or len(calendar_choices) > 500
                or not all(isinstance(k, str) and 0 < len(k) <= 300 and isinstance(v, bool)
                           for k, v in calendar_choices.items())):
            raise ValueError("Calendar choices must be on or off for each calendar.")
        current["calendar_choices"] = {**current["calendar_choices"], **calendar_choices}
    data = _read()
    data["meetings"] = current
    _write(data)
    return current


# Event keys end in "@<UTC start>", so sorting on that suffix keeps the latest.
_PROMPTED_MAX = 500


def get_prompted_events(day: str) -> set[str]:
    """Events the record banner already offered on local day `day`, so a
    relaunch doesn't ask again. Another day's list reads as empty."""
    raw = _read().get("record_prompted")
    if not isinstance(raw, dict) or raw.get("day") != day:
        return set()
    keys = raw.get("keys")
    return {k for k in keys if isinstance(k, str)} if isinstance(keys, list) else set()


def set_prompted_events(day: str, keys) -> None:
    latest = sorted(keys, key=lambda k: (k.rsplit("@", 1)[-1], k))[-_PROMPTED_MAX:]
    data = _read()
    data["record_prompted"] = {"day": day, "keys": latest}
    _write(data)
```

- [x] **Step 4: Bridge payload (`speakeasy/ui/meetings_bridge.py`)**

```python
    def settings_get_payload(self, params) -> dict:
        stored = settings.get_meeting_settings()
        calendars = self._calendar.calendars if self._calendar is not None else []
        return {"offerToRecord": stored["offer_to_record"],
                "detectCalls": stored["detect_calls"],
                "accounts": calendar_payloads.calendars_payload(
                    calendars, stored["calendar_choices"])}

    def settings_set_payload(self, params) -> dict:
        settings.set_meeting_settings(
            offer_to_record=params.get("offerToRecord"),
            detect_calls=params.get("detectCalls"),
            calendar_choices=params.get("calendars"))
        if params.get("calendars") is not None and self._calendar is not None:
            self._calendar.request_sync()
        return self.settings_get_payload({})
```

- [x] **Step 5: Frontend toggle**

- `frontend/src/mock/meetings.ts`: `export interface MeetingSettings { offerToRecord: boolean; detectCalls: boolean; accounts: CalendarAccountSettings[] }`, and add `detectCalls: true,` to the mock settings object next to `offerToRecord: true,`.
- `frontend/src/meetings/SettingsSheet.tsx`:
  - change the `onChange` patch type to `{ offerToRecord?: boolean; detectCalls?: boolean; calendars?: Record<string, boolean> }`;
  - destructure `detectCalls`;
  - add a second row directly after the offer-to-record row:

```tsx
      <div className={styles.toggleRow}>
        <span id="detect-calls-label">Offer to record calls in other apps</span>
        <Switch checked={detectCalls} onChange={(value) => onChange({ detectCalls: value })} ariaLabelledBy="detect-calls-label" />
      </div>
```

- `frontend/src/meetings/App.tsx`, `onChangeSettings`:
  - widen the patch type the same way;
  - in the optimistic merge, add `detectCalls: patch.detectCalls ?? prev.detectCalls,` next to `offerToRecord`;
  - pass `detectCalls` through to the bridge call wherever `offerToRecord` is passed.
- Delete `frontend/prompt.html` and `frontend/src/prompt/`, and remove the `prompt:` line from `frontend/vite.config.ts`. Then `grep -rn "prompt.html\|src/prompt" frontend scripts packaging speakeasy` must print nothing.

- [x] **Step 6: Run the tests and the frontend build**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_settings.py tests/test_meetings_bridge.py`, expect PASS.
Run: `npm --prefix frontend run build`, expect success with no type errors.
Then run the full suite (timeout 300000) and expect all tests to pass.

- [x] **Step 7: Commit**

```bash
git add -A speakeasy/settings.py speakeasy/ui/meetings_bridge.py frontend tests/test_settings.py tests/test_meetings_bridge.py
git commit -m "Settings: call-detection toggle and per-day prompted events; drop web prompt mock"
```

**Review mutations (Opus):**
- default `detect_calls` to `False`;
- drop the `raw.get("day") != day` check;
- keep the first 500 keys instead of the latest;
- skip the `isinstance(detect_calls, bool)` check;
- omit `detectCalls` from `settings_get_payload`.

---

### Task 3: Pure banner coordinator

**Files:**
- Create: `speakeasy/record_prompt.py`
- Test: `tests/test_record_prompt.py`

**Interfaces:**
- Consumes:
  - `calendar_match.prompt_candidates(events, now, prompted)`, `calendar_match.pick_event(events, now)` and `calendar_match.event_start(e)`;
  - `meeting_library.CalendarEvent` and `EventPerson(name, email, role)`.
- Produces:
  - `Offer(kind: str, events: tuple[CalendarEvent, ...], shown_at: datetime)`;
  - `CallEnded()`;
  - `BannerText(title, subtitle, primary, secondary, more: tuple[str, ...])`;
  - `banner_text(banner, now) -> BannerText`;
  - `RecordPromptCoordinator(prompted: set[str])` with:
    - attributes `.banner` and `.prompted`;
    - methods `engine_state(state: str)`, `calendar_tick(events, now, *, offer_enabled)`, `call_observed(using, now, events, *, detect_enabled)`, `record(index=0) -> str | None`, `not_now()`, `stop()` and `keep()`.
  - The engine states it reacts to are the `State.value` strings `"ready"`, `"meeting_recording"` and `"meeting_processing"`.

- [x] **Step 1: Write the failing tests (`tests/test_record_prompt.py`)**

```python
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import record_prompt as rp
from speakeasy.meeting_library import CalendarEvent, EventPerson

UTC = timezone.utc
T0 = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ev(key, start=T0, minutes=30, people=1, title=None):
    return CalendarEvent(
        key, "Work", title or key, start.strftime(FMT),
        (start + timedelta(minutes=minutes)).strftime(FMT), False, False, [],
        other_attendees=people or None,
        people=[EventPerson(f"P{i}", f"p{i}@example.test", "attendee") for i in range(people)])


def at(minutes=0, seconds=0):
    return T0 + timedelta(minutes=minutes, seconds=seconds)


def coord(state="ready", prompted=()):
    c = rp.RecordPromptCoordinator(set(prompted))
    c.engine_state(state)
    return c


def observe(c, using, t, events=(), detect=True):
    c.call_observed(using, t, list(events), detect_enabled=detect)


# -- calendar offers --------------------------------------------------------

def test_calendar_offer_needs_people_and_prefers_nearest():
    c = coord()
    c.calendar_tick([ev("solo", people=0), ev("far", at(-4)), ev("near", at(1))],
                    at(0), offer_enabled=True)
    assert isinstance(c.banner, rp.Offer) and c.banner.kind == "calendar"
    assert [e.event_key for e in c.banner.events] == ["near", "far"]


def test_calendar_offer_off_or_not_ready_shows_nothing():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=False)
    assert c.banner is None
    for state in ("recording", "transcribing", "paused", "meeting_recording", "loading"):
        c = coord(state)
        c.calendar_tick([ev("a")], at(0), offer_enabled=True)
        assert c.banner is None, state


def test_not_now_marks_every_offered_event_prompted():
    events = [ev("a"), ev("b", at(1))]
    c = coord()
    c.calendar_tick(events, at(0), offer_enabled=True)
    c.not_now()
    assert c.banner is None and c.prompted == {"a", "b"}
    c.calendar_tick(events, at(1), offer_enabled=True)
    assert c.banner is None


def test_offer_expires_after_five_minutes():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    c.calendar_tick([ev("a")], at(4, 59), offer_enabled=True)
    assert c.banner is not None
    c.calendar_tick([ev("a")], at(5), offer_enabled=True)
    assert c.banner is None and "a" in c.prompted


def test_record_returns_chosen_key_and_closes():
    c = coord()
    c.calendar_tick([ev("a"), ev("b", at(1))], at(0), offer_enabled=True)
    assert c.record(1) == "b"
    assert c.banner is None and c.prompted == {"a", "b"}


def test_record_without_offer_is_an_error():
    with pytest.raises(RuntimeError):
        coord().record()


def test_starting_a_meeting_closes_the_offer():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    c.engine_state("meeting_recording")
    assert c.banner is None and "a" in c.prompted


def test_turning_calendar_offers_off_hides_without_marking():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    c.calendar_tick([ev("a")], at(0, 30), offer_enabled=False)
    assert c.banner is None and "a" not in c.prompted


# -- call offers ------------------------------------------------------------

def test_call_offer_after_ten_seconds_of_other_app_mic():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 5))
    assert c.banner is None
    observe(c, True, at(0, 10))
    assert c.banner.kind == "call" and c.banner.events == ()


def test_brief_mic_use_never_offers():
    c = coord()
    observe(c, True, at(0))
    observe(c, False, at(0, 5))
    observe(c, True, at(0, 6))
    observe(c, True, at(0, 15))
    assert c.banner is None


def test_call_offer_links_matching_event_with_people():
    c = coord()
    observe(c, True, at(0), [ev("solo", at(-3), people=0), ev("a", at(-3))])
    observe(c, True, at(0, 10), [ev("solo", at(-3), people=0), ev("a", at(-3))])
    assert [e.event_key for e in c.banner.events] == ["a"]


def test_call_for_an_already_prompted_event_is_not_offered():
    c = coord(prompted={"a"})
    observe(c, True, at(0), [ev("a", at(-3))])
    observe(c, True, at(0, 10), [ev("a", at(-3))])
    assert c.banner is None


def test_one_call_offer_per_call():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    c.not_now()
    observe(c, True, at(1))
    observe(c, True, at(3))
    assert c.banner is None
    observe(c, False, at(4))
    observe(c, False, at(5))          # free for 60 s: that call is over
    observe(c, True, at(5, 1))
    observe(c, True, at(5, 11))
    assert c.banner.kind == "call"


def test_call_offer_hides_when_the_call_ends():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(0, 20))
    assert c.banner is not None
    observe(c, False, at(1, 20))
    assert c.banner is None


def test_call_detection_off_never_offers_and_hides():
    c = coord()
    observe(c, True, at(0), detect=False)
    observe(c, True, at(0, 10), detect=False)
    assert c.banner is None
    observe(c, True, at(0, 20))
    observe(c, True, at(0, 30))
    assert c.banner is not None
    observe(c, True, at(0, 35), detect=False)
    assert c.banner is None


def test_not_ready_never_offers_call():
    c = coord("recording")              # a dictation take holds our own mic
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    assert c.banner is None


def test_calendar_offer_is_not_replaced_by_call_offer():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    assert c.banner.kind == "calendar"


# -- call ended ---------------------------------------------------------------

def test_call_ended_after_sixty_seconds_free():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, False, at(1))
    observe(c, False, at(1, 59))
    assert c.banner is None
    observe(c, False, at(2))
    assert isinstance(c.banner, rp.CallEnded)


def test_no_call_ended_without_a_call():
    c = coord("meeting_recording")
    observe(c, False, at(0))
    observe(c, False, at(5))
    assert c.banner is None


def test_mic_resuming_hides_call_ended():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, False, at(1))
    observe(c, False, at(2))
    observe(c, True, at(2, 5))
    assert c.banner is None


def test_keep_recording_waits_for_the_next_call_end():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, False, at(1))
    observe(c, False, at(2))
    c.keep()
    observe(c, False, at(3))
    assert c.banner is None
    observe(c, True, at(4))
    observe(c, False, at(4, 1))
    observe(c, False, at(5, 1))
    assert isinstance(c.banner, rp.CallEnded)


def test_stop_clears_call_ended():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, False, at(1))
    observe(c, False, at(2))
    c.stop()
    assert c.banner is None


def test_call_ended_needs_call_detection_on():
    c = coord("meeting_recording")
    observe(c, True, at(0), detect=False)
    observe(c, False, at(1), detect=False)
    observe(c, False, at(2), detect=False)
    assert c.banner is None


def test_leaving_recording_resets_call_tracking():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, False, at(1))
    observe(c, False, at(2))
    c.engine_state("meeting_processing")
    assert c.banner is None
    c.engine_state("meeting_recording")
    observe(c, False, at(10))
    assert c.banner is None


def test_a_recorded_call_is_not_offered_after_recording():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    c.engine_state("meeting_processing")
    c.engine_state("ready")
    observe(c, True, at(1))
    observe(c, True, at(1, 30))
    assert c.banner is None


# -- text -------------------------------------------------------------------

def test_banner_text():
    cal = rp.Offer("calendar", (ev("a", at(1), people=2, title="VFA Leads Sync Up"),
                               ev("b", title="Weekly Tactical")), at(0))
    text = rp.banner_text(cal, at(0))
    assert (text.title, text.subtitle) == ("VFA Leads Sync Up", "Starts in 1 min · 2 invited")
    assert (text.primary, text.secondary, text.more) == ("Record", "Not Now", ("Weekly Tactical",))
    assert rp.banner_text(cal, at(1)).subtitle.startswith("Starting now")
    assert rp.banner_text(cal, at(4)).subtitle.startswith("Started 3 min ago")
    call = rp.banner_text(rp.Offer("call", (), at(0)), at(0))
    assert (call.title, call.subtitle) == ("Record this call?", "Another app is using the microphone")
    linked = rp.banner_text(rp.Offer("call", (ev("a", title="SSO follow up"),), at(0)), at(0))
    assert linked.title == "SSO follow up" and linked.primary == "Record"
    ended = rp.banner_text(rp.CallEnded(), at(0))
    assert (ended.title, ended.subtitle, ended.primary, ended.secondary) == (
        "Call ended", "Stop recording?", "Stop", "Keep Recording")
```

- [x] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_record_prompt.py`
Expected: FAIL (`No module named speakeasy.record_prompt`).

- [x] **Step 3: Implement `speakeasy/record_prompt.py`**

```python
"""What the record banner shows. Pure and main-thread only: the controller
feeds it calendar ticks, call observations, engine states and clicks, then
draws `banner`. Speakeasy never records by itself — every path that starts or
stops a recording begins with a click the controller reports here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import calendar_match
from .meeting_library import CalendarEvent

OFFER_TIMEOUT = timedelta(minutes=5)
# Brief mic use (another dictation app, a voice memo) is not a call.
CALL_START = timedelta(seconds=10)
CALL_END = timedelta(seconds=60)

READY = "ready"
RECORDING = "meeting_recording"
PROCESSING = "meeting_processing"


@dataclass(frozen=True)
class Offer:
    kind: str                          # "calendar" or "call"
    events: tuple[CalendarEvent, ...]  # first is shown; the rest go in "N more"
    shown_at: datetime


@dataclass(frozen=True)
class CallEnded:
    pass


@dataclass(frozen=True)
class BannerText:
    title: str
    subtitle: str
    primary: str
    secondary: str
    more: tuple[str, ...] = ()


def _with_people(events) -> list[CalendarEvent]:
    # Personal blocks ("pickup", "Outside office hours") invite nobody else.
    return [e for e in events if e.people]


def _when(event: CalendarEvent, now: datetime) -> str:
    minutes = round((calendar_match.event_start(event) - now).total_seconds() / 60)
    if minutes >= 1:
        return f"Starts in {minutes} min"
    if minutes <= -1:
        return f"Started {-minutes} min ago"
    return "Starting now"


def banner_text(banner, now: datetime) -> BannerText:
    if isinstance(banner, CallEnded):
        return BannerText("Call ended", "Stop recording?", "Stop", "Keep Recording")
    if banner.kind == "call":
        if banner.events:
            return BannerText(banner.events[0].title, "Call in progress", "Record", "Not Now")
        return BannerText("Record this call?", "Another app is using the microphone",
                          "Record", "Not Now")
    first = banner.events[0]
    return BannerText(first.title, f"{_when(first, now)} · {len(first.people)} invited",
                      "Record", "Not Now", tuple(e.title for e in banner.events[1:]))


class RecordPromptCoordinator:
    def __init__(self, prompted: set[str]) -> None:
        self.prompted = set(prompted)
        self.banner: Offer | CallEnded | None = None
        self._state: str | None = None
        self._using_since: datetime | None = None
        self._idle_since: datetime | None = None
        self._call_offered = False      # this call was offered (or recorded) already
        self._heard_call = False        # another app used the mic during this recording
        self._ended_dismissed = False   # Keep Recording, until the mic is used again

    # -- inputs ---------------------------------------------------------------

    def engine_state(self, state: str) -> None:
        if state != RECORDING:
            self._heard_call = False
            self._ended_dismissed = False
            if isinstance(self.banner, CallEnded):
                self.banner = None
        if state in (RECORDING, PROCESSING) and isinstance(self.banner, Offer):
            self._close_offer()   # started by hand (menu, Dock, Today view)
        self._state = state

    def calendar_tick(self, events, now: datetime, *, offer_enabled: bool) -> None:
        self._expire(now)
        if not offer_enabled:
            if isinstance(self.banner, Offer) and self.banner.kind == "calendar":
                self.banner = None
            return
        if self._state != READY or self.banner is not None:
            return
        candidates = _with_people(calendar_match.prompt_candidates(events, now, self.prompted))
        if candidates:
            candidates.sort(key=lambda e: (
                abs((calendar_match.event_start(e) - now).total_seconds()), e.event_key))
            self.banner = Offer("calendar", tuple(candidates), now)

    def call_observed(self, using: bool, now: datetime, events, *,
                      detect_enabled: bool) -> None:
        if using:
            self._idle_since = None
            if self._using_since is None:
                self._using_since = now
        else:
            self._using_since = None
            if self._idle_since is None:
                self._idle_since = now
        call_over = self._idle_since is not None and now - self._idle_since >= CALL_END
        if call_over:
            self._call_offered = False
        if not detect_enabled:
            if isinstance(self.banner, CallEnded) or (
                    isinstance(self.banner, Offer) and self.banner.kind == "call"):
                self.banner = None
            return
        self._expire(now)
        if self._state == RECORDING:
            self._watch_recording(using, call_over)
            return
        if isinstance(self.banner, Offer) and self.banner.kind == "call" and call_over:
            self.banner = None   # the call ended before anyone answered
            return
        if (self._state != READY or self.banner is not None or self._call_offered
                or self._using_since is None or now - self._using_since < CALL_START):
            return
        self._call_offered = True
        event = calendar_match.pick_event(_with_people(events), now)
        if event is not None and event.event_key in self.prompted:
            return   # the calendar banner already asked about this meeting
        self.banner = Offer("call", (event,) if event is not None else (), now)

    # -- clicks ---------------------------------------------------------------

    def record(self, index: int = 0) -> str | None:
        """The event key to link (None: let the engine match), and close."""
        offer = self.banner
        if not isinstance(offer, Offer):
            raise RuntimeError("no record offer is showing")
        key = offer.events[index].event_key if offer.events else None
        self._close_offer()
        return key

    def not_now(self) -> None:
        if isinstance(self.banner, Offer):
            self._close_offer()

    def stop(self) -> None:
        if isinstance(self.banner, CallEnded):
            self.banner = None

    def keep(self) -> None:
        if isinstance(self.banner, CallEnded):
            self.banner = None
            self._ended_dismissed = True

    # -- internals --------------------------------------------------------------

    def _watch_recording(self, using: bool, call_over: bool) -> None:
        if using:
            self._heard_call = True
            self._call_offered = True    # recorded, so not offered again after
            self._ended_dismissed = False
            if isinstance(self.banner, CallEnded):
                self.banner = None
            return
        if (self._heard_call and call_over and self.banner is None
                and not self._ended_dismissed):
            self.banner = CallEnded()

    def _expire(self, now: datetime) -> None:
        if isinstance(self.banner, Offer) and now - self.banner.shown_at >= OFFER_TIMEOUT:
            self._close_offer()

    def _close_offer(self) -> None:
        self.prompted.update(e.event_key for e in self.banner.events)
        self.banner = None
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_record_prompt.py tests/test_calendar_match.py`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add speakeasy/record_prompt.py tests/test_record_prompt.py
git commit -m "Record prompt: pure coordinator for calendar, call and call-ended banners"
```

**Review mutations (Opus):**
- remove `_with_people`;
- sort candidates by `start_utc` instead of distance;
- `CALL_START = 0`;
- drop `self._call_offered = True` in `_watch_recording`;
- remove the `_heard_call` condition;
- `_close_offer` without `prompted.update`;
- `engine_state` not closing the offer on `RECORDING`;
- `_ended_dismissed` not reset when the mic is used again.

---

### Task 4: Native banner panel, controller and app wiring

**Files:**
- Create: `speakeasy/ui/record_prompt_panel.py`, `speakeasy/ui/record_prompt_controller.py`
- Modify: `speakeasy/ui/menubar.py` (`AppDelegate.initWithProfileName_` ~L577, `applicationDidFinishLaunching_` ~L610–716, `applicationWillTerminate_` ~L718)
- Test: `tests/test_record_prompt_controller.py`, `tests/test_record_prompt_panel.py`

**Interfaces:**
- Consumes:
  - from Task 1, `call_detect.CallProbe`;
  - from Task 2, `settings.get_meeting_settings()`, `get_prompted_events` and `set_prompted_events`;
  - from Task 3, `RecordPromptCoordinator`, `Offer`, `CallEnded` and `banner_text`;
  - `MeetingOptions(calendar_event_key=...)`;
  - `engine.state.value`, `engine.library.calendar_events_overlapping(start, end)`, `engine.begin_meeting(options)` and `engine.end_meeting()`.
- Produces:
  - `RecordPromptPanel.alloc().initWithTarget_(target)` with:
    - python methods `show(title, subtitle, primary, secondary, more)`, `show_confirmation(text)` and `hide()`;
    - callbacks to the target: `bannerPrimary_(sender)`, `bannerSecondary_(sender)` and `bannerMore_(item)`, where `item.tag()` is the event index ≥ 1.
  - `RecordPromptController.alloc().initWithEngine_(engine)` with selectors `engineStateChanged_(state_name)`, `tick_(timer)` and `callObserved_(using)`, plus python method `shutdown()`.

- [x] **Step 1: Write the failing controller tests (`tests/test_record_prompt_controller.py`)**

```python
from datetime import datetime, timedelta, timezone

from speakeasy import settings
from speakeasy.meeting_library import CalendarEvent, EventPerson
from speakeasy.meeting_options import MeetingOptions
from speakeasy.ui import record_prompt_controller as rpc

UTC = timezone.utc
NOW = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ev(key, start=NOW, people=1, title="VFA Leads Sync Up"):
    return CalendarEvent(key, "Work", title, start.strftime(FMT),
                         (start + timedelta(minutes=30)).strftime(FMT), False, False, [],
                         other_attendees=people or None,
                         people=[EventPerson("Anju", None, "attendee")] * people)


class FakePanel:
    def __init__(self):
        self.calls = []

    def show(self, title, subtitle, primary, secondary, more):
        self.calls.append(("show", title, primary, secondary, tuple(more)))

    def show_confirmation(self, text):
        self.calls.append(("confirm", text))

    def hide(self):
        self.calls.append(("hide",))


class FakeProbe:
    started = stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeLibrary:
    def __init__(self, events):
        self.events = events

    def calendar_events_overlapping(self, start, end):
        return list(self.events)


class FakeEngine:
    def __init__(self, state="ready", events=()):
        self.state = type("S", (), {"value": state})()
        self.library = FakeLibrary(events)
        self.begun, self.ended = [], 0

    def begin_meeting(self, options):
        self.begun.append(options)

    def end_meeting(self):
        self.ended += 1


class Item:
    def __init__(self, tag):
        self._tag = tag

    def tag(self):
        return self._tag


def make(monkeypatch, engine, clock=None):
    panel, probe = FakePanel(), FakeProbe()
    times = clock or [NOW]
    monkeypatch.setattr(rpc, "_make_panel", lambda target: panel)
    monkeypatch.setattr(rpc, "_make_probe", lambda controller: probe)
    monkeypatch.setattr(rpc, "_now", lambda: times[0])
    monkeypatch.setattr(rpc, "_today", lambda: "2026-10-02")
    c = rpc.RecordPromptController.alloc().initWithEngine_(engine)
    return c, panel, probe, times


def test_calendar_offer_records_the_linked_event(monkeypatch):
    engine = FakeEngine(events=[ev("k1"), ev("k2", NOW + timedelta(minutes=1), title="Tactical")])
    c, panel, probe, _ = make(monkeypatch, engine)
    assert probe.started
    c.tick_(None)
    assert panel.calls[-1] == ("show", "VFA Leads Sync Up", "Record", "Not Now", ("Tactical",))
    c.bannerMore_(Item(1))
    assert engine.begun == [MeetingOptions(calendar_event_key="k2")]
    assert panel.calls[-1] == ("confirm", "Recording")
    assert settings.get_prompted_events("2026-10-02") == {"k1", "k2"}
    c.shutdown()
    assert probe.stopped


def test_not_now_hides_and_is_remembered_across_relaunch(monkeypatch):
    engine = FakeEngine(events=[ev("k1")])
    c, panel, _, _ = make(monkeypatch, engine)
    c.tick_(None)
    c.bannerSecondary_(None)
    assert panel.calls[-1] == ("hide",) and engine.begun == []
    c.shutdown()
    c2, panel2, _, _ = make(monkeypatch, engine)
    c2.tick_(None)
    assert [x for x in panel2.calls if x[0] == "show"] == []
    c2.shutdown()


def test_calendar_offer_respects_setting(monkeypatch):
    settings.set_meeting_settings(offer_to_record=False)
    c, panel, _, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    assert [x for x in panel.calls if x[0] == "show"] == []
    c.shutdown()


def test_call_offer_records_with_engine_matching(monkeypatch):
    c, panel, _, times = make(monkeypatch, FakeEngine())
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=10)
    c.callObserved_(True)
    assert panel.calls[-1][:2] == ("show", "Record this call?")
    c.bannerPrimary_(None)
    assert c.engine.begun == [MeetingOptions(calendar_event_key=None)]
    c.shutdown()


def test_call_ended_stop_ends_meeting(monkeypatch):
    engine = FakeEngine(state="meeting_recording")
    c, panel, _, times = make(monkeypatch, engine)
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=30)
    c.callObserved_(False)
    times[0] = NOW + timedelta(seconds=90)
    c.callObserved_(False)
    assert panel.calls[-1] == ("show", "Call ended", "Stop", "Keep Recording", ())
    c.bannerPrimary_(None)
    assert engine.ended == 1 and panel.calls[-1] == ("hide",)
    c.shutdown()


def test_probe_wanted_follows_setting_and_state(monkeypatch):
    engine = FakeEngine()
    c, _, _, _ = make(monkeypatch, engine)
    assert c.probe_wanted is True
    c.engineStateChanged_("meeting_processing")
    assert c.probe_wanted is False
    c.engineStateChanged_("meeting_recording")
    assert c.probe_wanted is True
    settings.set_meeting_settings(detect_calls=False)
    c.tick_(None)
    assert c.probe_wanted is False
    c.shutdown()


def test_library_error_means_no_events(monkeypatch):
    import sqlite3

    engine = FakeEngine()

    def broken(start, end):
        raise sqlite3.OperationalError("locked")

    engine.library.calendar_events_overlapping = broken
    c, panel, _, _ = make(monkeypatch, engine)
    c.tick_(None)
    assert [x for x in panel.calls if x[0] == "show"] == []
    c.shutdown()
```

- [x] **Step 2: Write the failing panel smoke tests (`tests/test_record_prompt_panel.py`)**

```python
from AppKit import NSApplication, NSWindowStyleMaskNonactivatingPanel

from speakeasy.ui.record_prompt_panel import RecordPromptPanel

NSApplication.sharedApplication()   # windows need the shared app object


class Target:
    def __init__(self):
        self.calls = []

    def bannerPrimary_(self, sender):
        self.calls.append("primary")

    def bannerSecondary_(self, sender):
        self.calls.append("secondary")

    def bannerMore_(self, item):
        self.calls.append(("more", item.tag()))


def test_panel_never_takes_focus_and_forwards_clicks():
    target = Target()
    p = RecordPromptPanel.alloc().initWithTarget_(target)
    assert p._panel.styleMask() & NSWindowStyleMaskNonactivatingPanel
    assert not p._panel.canBecomeMainWindow()
    p.show("VFA Leads Sync Up", "Starting now · 3 invited", "Record", "Not Now", ["Tactical"])
    assert p._title.stringValue() == "VFA Leads Sync Up"
    assert p._primary.title() == "Record" and not p._primary.isHidden()
    assert p._more.title() == "1 more" and not p._more.isHidden()
    p.primaryClicked_(None)
    p.secondaryClicked_(None)
    item = type("I", (), {"tag": lambda self: 1})()
    p.moreChosen_(item)
    assert target.calls == ["primary", "secondary", ("more", 1)]
    p.show("Call ended", "Stop recording?", "Stop", "Keep Recording", [])
    assert p._more.isHidden()
    p.show_confirmation("Recording")
    assert p._primary.isHidden() and p._subtitle.stringValue() == "Recording"
    p.hide()
    assert not p._panel.isVisible()
```

- [x] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_record_prompt_controller.py tests/test_record_prompt_panel.py`
Expected: FAIL (modules missing).

- [x] **Step 4: Implement `speakeasy/ui/record_prompt_panel.py`**

```python
"""The record banner: a borderless, non-activating glass panel in the
top-right corner. It never becomes key, so the meeting app keeps keyboard
focus, and its ClickyButtons act on the first click. Main-thread only; it
draws what RecordPromptController tells it and forwards clicks to it."""

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSColor,
    NSFontWeightSemibold,
    NSLineBreakByTruncatingTail,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSAnimationContext,
)
from Foundation import NSMakePoint, NSMakeRect, NSObject, NSTimer

from .glass import ClickyButton, make_label

WIDTH, HEIGHT, MARGIN = 360.0, 92.0, 12.0
CONFIRM_SECONDS = 2.0


class RecordPromptPanel(NSObject):
    def initWithTarget_(self, target):
        self = objc.super(RecordPromptPanel, self).init()
        if self is None:
            return None
        self.target = target
        self._more_titles = []
        self._hide_timer = None
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), style, NSBackingStoreBuffered, False)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        effect = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setMaterial_(NSVisualEffectMaterialPopover)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(14.0)
        effect.layer().setMasksToBounds_(True)
        panel.setContentView_(effect)

        self._title = make_label("", 13.0, NSFontWeightSemibold)
        self._title.setFrame_(NSMakeRect(16, 58, WIDTH - 32, 18))
        self._title.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._subtitle = make_label("", 12.0, secondary=True)
        self._subtitle.setFrame_(NSMakeRect(16, 40, WIDTH - 32, 16))
        self._subtitle.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._primary = self._button(b"primaryClicked:", NSMakeRect(WIDTH - 16 - 96, 8, 96, 28))
        # Coral primary, as in the Meetings window.
        self._primary.setBezelColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.45, 0.38, 1.0))
        self._secondary = self._button(
            b"secondaryClicked:", NSMakeRect(WIDTH - 16 - 96 - 8 - 128, 8, 128, 28))
        self._more = self._button(b"moreClicked:", NSMakeRect(12, 8, 84, 28))
        for view in (self._title, self._subtitle, self._primary, self._secondary, self._more):
            effect.addSubview_(view)
        self._panel = panel
        return self

    @objc.python_method
    def _button(self, action, frame):
        button = ClickyButton.alloc().initWithFrame_(frame)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        return button

    # -- drawing (controller) ---------------------------------------------------

    @objc.python_method
    def show(self, title, subtitle, primary, secondary, more):
        self._cancel_hide_timer()
        self._title.setStringValue_(title)
        self._subtitle.setStringValue_(subtitle)
        self._primary.setTitle_(primary)
        self._secondary.setTitle_(secondary)
        self._primary.setHidden_(False)
        self._secondary.setHidden_(False)
        self._more_titles = list(more)
        self._more.setTitle_(f"{len(self._more_titles)} more")
        self._more.setHidden_(not self._more_titles)
        self._place()
        if not self._panel.isVisible():
            self._panel.setAlphaValue_(0.0)
            self._panel.orderFrontRegardless()
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.18)
            self._panel.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()

    @objc.python_method
    def show_confirmation(self, text):
        self._subtitle.setStringValue_(text)
        for button in (self._primary, self._secondary, self._more):
            button.setHidden_(True)
        self._cancel_hide_timer()
        self._hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            CONFIRM_SECONDS, self, b"confirmationDone:", None, False)

    @objc.python_method
    def hide(self):
        self._cancel_hide_timer()
        # Ordered out at once: a faded-but-visible panel would still eat clicks.
        self._panel.orderOut_(None)

    def confirmationDone_(self, timer):
        self._hide_timer = None
        self.hide()

    @objc.python_method
    def _cancel_hide_timer(self):
        if self._hide_timer is not None:
            self._hide_timer.invalidate()
            self._hide_timer = None

    @objc.python_method
    def _place(self):
        screen = (NSScreen.mainScreen() or NSScreen.screens()[0]).visibleFrame()
        self._panel.setFrameOrigin_(NSMakePoint(
            screen.origin.x + screen.size.width - WIDTH - MARGIN,
            screen.origin.y + screen.size.height - HEIGHT - MARGIN))

    # -- clicks -------------------------------------------------------------

    def primaryClicked_(self, sender):
        self.target.bannerPrimary_(sender)

    def secondaryClicked_(self, sender):
        self.target.bannerSecondary_(sender)

    def moreClicked_(self, sender):
        menu = NSMenu.alloc().initWithTitle_("")
        for index, title in enumerate(self._more_titles, start=1):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                f"Record ‘{title}’", b"moreChosen:", "")
            item.setTarget_(self)
            item.setTag_(index)
            menu.addItem_(item)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(0, 0), sender)

    def moreChosen_(self, item):
        self.target.bannerMore_(item)
```

If `NSBezelStyleRounded` fails to import from the pinned PyObjC (it was renamed `NSBezelStylePush` in the macOS 14 SDK), use `NSBezelStylePush`. Both have the value 1.

- [x] **Step 5: Implement `speakeasy/ui/record_prompt_controller.py`**

```python
"""Main-thread glue for the record banner. It feeds RecordPromptCoordinator
calendar ticks (30 s, cached table only), call-probe answers and engine
states, draws its banner on RecordPromptPanel, and turns clicks into
begin_meeting/end_meeting — the only way this feature records anything."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import objc
from Foundation import NSObject, NSTimer

from .. import settings
from ..call_detect import CallProbe
from ..meeting_options import MeetingOptions
from ..record_prompt import CallEnded, Offer, RecordPromptCoordinator, banner_text

TICK_SECONDS = 30.0
# Wide enough for both prompt_candidates (start −2…+5 min) and pick_event
# (start −10 min … end).
_EVENT_WINDOW = timedelta(minutes=15)
_PROBED_STATES = {"ready", "meeting_recording"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _make_panel(target):
    from .record_prompt_panel import RecordPromptPanel

    return RecordPromptPanel.alloc().initWithTarget_(target)


def _make_probe(controller):
    return CallProbe(
        on_result=lambda using: controller.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"callObserved:", using, False),
        wanted=lambda: controller.probe_wanted)


class RecordPromptController(NSObject):
    def initWithEngine_(self, engine):
        self = objc.super(RecordPromptController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.coordinator = RecordPromptCoordinator(settings.get_prompted_events(_today()))
        self._saved_prompted = set(self.coordinator.prompted)
        self._shown = None
        self.probe_wanted = False
        self.panel = _make_panel(self)
        self.probe = _make_probe(self)
        self.probe.start()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            TICK_SECONDS, self, b"tick:", None, True)
        self.engineStateChanged_(engine.state.value)
        return self

    # -- inputs (main thread) ---------------------------------------------------

    def engineStateChanged_(self, state_name):
        state = str(state_name)
        self.coordinator.engine_state(state)
        self._update_probe(state, settings.get_meeting_settings())
        self._render()

    def tick_(self, timer):
        now = _now()
        prefs = settings.get_meeting_settings()
        self.coordinator.calendar_tick(self._events_near(now), now,
                                       offer_enabled=prefs["offer_to_record"])
        self._update_probe(self.engine.state.value, prefs)
        self._render()

    def callObserved_(self, using):
        now = _now()
        self.coordinator.call_observed(
            bool(using), now, self._events_near(now),
            detect_enabled=settings.get_meeting_settings()["detect_calls"])
        self._render()

    # -- clicks (from the panel) ---------------------------------------------------

    def bannerPrimary_(self, sender):
        banner = self.coordinator.banner
        if isinstance(banner, Offer):
            self._record(0)
        elif isinstance(banner, CallEnded):
            self.coordinator.stop()
            self.engine.end_meeting()
            self._render()

    def bannerSecondary_(self, sender):
        banner = self.coordinator.banner
        if isinstance(banner, Offer):
            self.coordinator.not_now()
        elif isinstance(banner, CallEnded):
            self.coordinator.keep()
        self._render()

    def bannerMore_(self, item):
        if isinstance(self.coordinator.banner, Offer):
            self._record(int(item.tag()))

    @objc.python_method
    def shutdown(self):
        self._timer.invalidate()
        self.probe.stop()
        self.panel.hide()

    # -- internals ---------------------------------------------------------------

    @objc.python_method
    def _record(self, index):
        key = self.coordinator.record(index)
        self.engine.begin_meeting(MeetingOptions(calendar_event_key=key))
        self._persist()
        # The panel hides itself after the confirmation; nothing to re-draw.
        self._shown = None
        self.panel.show_confirmation("Recording")

    @objc.python_method
    def _update_probe(self, state, prefs):
        self.probe_wanted = bool(prefs["detect_calls"]) and state in _PROBED_STATES

    @objc.python_method
    def _events_near(self, now):
        try:
            return self.engine.library.calendar_events_overlapping(
                now - _EVENT_WINDOW, now + _EVENT_WINDOW)
        except (sqlite3.Error, ValueError):
            return []

    @objc.python_method
    def _persist(self):
        if self.coordinator.prompted != self._saved_prompted:
            settings.set_prompted_events(_today(), self.coordinator.prompted)
            self._saved_prompted = set(self.coordinator.prompted)

    @objc.python_method
    def _render(self):
        self._persist()
        banner = self.coordinator.banner
        if banner is self._shown:
            return
        self._shown = banner
        if banner is None:
            self.panel.hide()
            return
        text = banner_text(banner, _now())
        self.panel.show(text.title, text.subtitle, text.primary, text.secondary, list(text.more))
```

- [x] **Step 6: Wire it into `AppDelegate` (`speakeasy/ui/menubar.py`)**

- In `initWithProfileName_`, add `self.record_prompt = None`.
- In `applicationDidFinishLaunching_`, after `self.main_window.window_owner = self.controller`:

```python
        from .record_prompt_controller import RecordPromptController

        # Offers to record when a calendar meeting starts or another app
        # starts using the mic; never records without a click.
        self.record_prompt = RecordPromptController.alloc().initWithEngine_(engine)
        record_prompt = self.record_prompt
```

- Inside `on_state_changed(state)`, add a third hop:

```python
            record_prompt.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"engineStateChanged:", state.value, False
            )
```

- After `main_window.engineStateChanged_(engine.state.value)` near the end, add `self.record_prompt.engineStateChanged_(engine.state.value)`.
- In `applicationWillTerminate_`, before the engine shutdown:

```python
        if getattr(self, "record_prompt", None) is not None:
            self.record_prompt.shutdown()
```

- [x] **Step 7: Run the tests, the full suite and an import check**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_record_prompt_controller.py tests/test_record_prompt_panel.py`, expect PASS.
Run the full suite (timeout 300000) and expect all tests to pass, including `test_mcp_mode_imports_nothing_heavy`.
Run: `.venv/bin/python -c "import speakeasy.ui.menubar"`, expect no error.

- [x] **Step 8: Commit**

```bash
git add speakeasy/ui/record_prompt_panel.py speakeasy/ui/record_prompt_controller.py speakeasy/ui/menubar.py tests/test_record_prompt_controller.py tests/test_record_prompt_panel.py
git commit -m "Record prompt: native non-activating banner, controller and app wiring"
```

**Review mutations (Opus):**
- `bannerMore_` records index 0;
- `_record` omits `calendar_event_key`;
- `_update_probe` ignores `detect_calls`;
- `_persist` never writes;
- `bannerPrimary_` on `CallEnded` not calling `end_meeting`;
- the panel built without `NSWindowStyleMaskNonactivatingPanel`.

Also check by reading that nothing calls `makeKeyAndOrderFront_` or `activateIgnoringOtherApps_` in the two new files.

---

### Task 5: Docs, install, live acceptance, checkpoint

**Files:**
- Modify: `README.md` (Calendar section ~L307–337), `AGENTS.md` (Hard constraints; Threading model), this plan (Execution notes)

- [x] **Step 1: README.** Add a `### Record prompts` subsection after `### Calendar` covering:
  - the calendar banner: 2 min before to 5 min after start, events with other invitees only, "N more", Not Now and the 5-minute timeout, remembered for the day;
  - the call banner: another app using the mic for 10 s; one offer per call;
  - "Call ended — Stop recording?" after 60 s without the call's mic, with no auto-stop;
  - both Settings toggles;
  - "Speakeasy never records without your click";
  - privacy: only "is another app using the mic" is checked, and app names/PIDs are never stored;
  - unavailable before macOS 14.2.
- [x] **Step 2: AGENTS.md.**
  - Under *Hard constraints*, add **Record prompts never record**: every `begin_meeting`/`end_meeting` from `record_prompt_controller.py` follows a banner click; `call_detect.py` reduces input PIDs to one boolean and nothing logs, stores or shows them.
  - Under *Threading model*, add **`call-probe`** (1 daemon thread, `call_detect.CallProbe`): every 5 s while wanted, runs the helper's `--list-input-pids` (1 s timeout) and `pgrep -P`, and hands a bool to the main thread; it never opens audio, and never touches the model, executors or UI.
  - Note that the banner panel is native, borderless and non-activating, and never made key.
- [x] **Step 3: Full suite (timeout 300000) and `npm --prefix frontend run build`.** Both must pass. Commit: `git commit -am "Docs: record prompts"`.
- [x] **Step 4: Opus whole-branch review** (`Agent(model: "opus")`): spec coverage, Global Constraints, Review Focus, and a mutation spot-check across tasks. Apply fixes with a Sonnet implementer, re-review, and record the outcome below.
- [ ] **Step 5: Install** (needs the user's go-ahead). Run `scripts/build_app.sh --install`; from a worktree, symlink `models` first. This stops Claude Desktop's Speakeasy MCP server, so remind the user to toggle the connector off and on. Then launch `/Applications/Speakeasy.app`.
- [ ] **Step 6: Live acceptance, launched and looked at.** The user does these by hand; screen control has been declined before. Record each result below.
  1. Meetings › Settings shows both toggles, and they persist after quit and relaunch.
  2. A real calendar meeting with invitees shows the banner within about 30 s of start − 2 min. Keep typing in Teams after it appears; Teams keeps focus. Record starts a recording, and the Dock shows "Recording · <title>".
  3. A Teams or Zoom call with no calendar event (e.g. a Teams test call) shows "Record this call?" within about 15 s. Not Now means it is not offered again during that call.
  4. While recording a call, leave it. "Call ended — Stop recording?" appears within about 65 s. Keep Recording hides it; leaving a second call and clicking Stop ends the meeting.
  5. Turn "Offer to record calls in other apps" off: no call banner appears.
  6. Dictation still pastes once in TextEdit, Codex and Teams. Insertion code is untouched, but check anyway.
- [ ] **Step 7: Checkpoint** (per CLAUDE.md): update this plan's status, commit, list loose ends, and give the `/clear` reminder. Merge only when the user says "merge and clean up".

### Task 6: Only apps that start using the mic count (follow-up, 2 Oct 2026)

**Why:** in the live check, GeForce NOW kept Core Audio input running while idle. Speakeasy treated it as a call that never ends. The result was a false "Record this call?" banner, no offers for real Teams calls, and no "Call ended". The user chose this rule: only apps that *start* using the mic count.

**Rule:**
- An app already using the mic at the first known answer after launch is ignored until it releases the mic.
- After a release, its next use counts.
- The app behind a call offer that is waved off (Not Now, or the 5-minute timeout) is ignored the same way. This covers GeForce NOW opened *after* Speakeasy: one offer, then it stays quiet.
- Recording, or a meeting started by hand, never ignores anything, so "Call ended" still works.
- **Trade-off:** a call already in progress when Speakeasy launches is not offered. Begin Meeting still works.

PIDs stay inside `call_detect.py` (Global Constraints). The controller only sends a "wave off" flag.

**Files:**
- Modify: `speakeasy/call_detect.py`, `speakeasy/record_prompt.py`, `speakeasy/ui/record_prompt_controller.py`, `README.md` (Record prompts), `AGENTS.md` (call-probe thread)
- Test: `tests/test_call_detect.py`, `tests/test_record_prompt.py`, `tests/test_record_prompt_controller.py`

- [ ] **Step 1: Failing tests.**

  `tests/test_call_detect.py` replaces `test_other_app_using_mic_ignores_speakeasy_processes` (keep that name for Review Focus 4) and adds the rest:

```python
def _watch(answers, own=frozenset({10})):
    seq = iter(answers)
    return call_detect.MicWatch(input_ids=lambda: next(seq), own_ids=lambda: set(own))


def test_other_app_using_mic_ignores_speakeasy_processes():
    w = _watch([set(), {10, 11}, {10, 12}], own={10, 11})
    assert [w(), w(), w()] == [False, False, True]


def test_apps_already_using_the_mic_are_ignored_until_they_release_it():
    w = _watch([{20}, {20}, {20, 30}, {20}, set(), {20}])
    assert [w(), w(), w(), w(), w(), w()] == [False, False, True, False, False, True]


def test_unknown_answers_pass_through_and_do_not_set_the_baseline():
    w = _watch([None, {20}, {20, 30}])
    assert [w(), w(), w()] == [None, False, True]


def test_waved_off_apps_are_ignored_until_they_release_the_mic():
    w = _watch([set(), {30}, {30}, {30, 40}, set(), {30}])
    assert [w(), w()] == [False, True]
    w.ignore_current()
    assert [w(), w(), w(), w()] == [False, True, False, True]


def test_probe_ignore_current_reaches_its_watch():
    p = call_detect.CallProbe(on_result=lambda v: None, wanted=lambda: False)
    assert isinstance(p.watch, call_detect.MicWatch)
    p.ignore_current()
    assert p.watch._ignore_requested
    q = call_detect.CallProbe(on_result=lambda v: None, wanted=lambda: False,
                              probe=lambda: True)
    assert q.watch is None
    q.ignore_current()        # no watch: nothing to do, no error
```

  `tests/test_record_prompt.py` gets:

```python
def test_waving_off_a_call_offer_is_reported_once():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    c.not_now()
    assert c.take_call_waved_off() is True
    assert c.take_call_waved_off() is False


def test_call_offer_timeout_counts_as_waving_off():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    c.calendar_tick([], at(5, 10), offer_enabled=True)
    assert c.banner is None and c.take_call_waved_off() is True


def test_recording_or_calendar_dismissal_is_not_waving_off_a_call():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    c.record()
    assert c.take_call_waved_off() is False
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    c.engine_state("meeting_recording")
    assert c.take_call_waved_off() is False
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    c.not_now()
    assert c.take_call_waved_off() is False
```

  `tests/test_record_prompt_controller.py`: `FakeProbe` gains `ignored = 0` and `def ignore_current(self): self.ignored += 1`. Then add:

```python
def test_not_now_on_a_call_offer_ignores_that_app(monkeypatch):
    c, panel, probe, times = make(monkeypatch, FakeEngine())
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=10)
    c.callObserved_(True)
    c.bannerSecondary_(None)
    assert probe.ignored == 1
    c.shutdown()


def test_not_now_on_a_calendar_offer_ignores_nothing(monkeypatch):
    c, panel, probe, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    c.bannerSecondary_(None)
    assert probe.ignored == 0
    c.shutdown()
```

- [ ] **Step 2: Run them to verify they fail.** `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_call_detect.py tests/test_record_prompt.py tests/test_record_prompt_controller.py`

- [ ] **Step 3: `speakeasy/call_detect.py`.** Replace `other_app_using_mic` with `MicWatch`. Keep `own_process_ids` unchanged. Update the module docstring's first paragraph to say an app counts only from when it starts using the mic.

```python
class MicWatch:
    """Is another app on a call? An app counts only from when it starts using
    the mic while Speakeasy watches. Apps already using it at the first known
    answer (GeForce NOW keeps input running while idle), and apps behind a
    waved-off call offer, are ignored until they release the mic. Called on
    the probe thread; the PIDs never leave this object."""

    def __init__(self, input_ids=None, own_ids=None) -> None:
        self._input_ids = input_ids or system_audio.input_process_ids
        self._own_ids = own_ids or own_process_ids
        self._ignored: set[int] | None = None   # None until the first known answer
        self._ignore_requested = False

    def ignore_current(self) -> None:
        """Main thread: ignore the apps using the mic now. A plain flag that
        the probe thread applies on its next answer."""
        self._ignore_requested = True

    def __call__(self) -> bool | None:
        active = self._input_ids()
        if active is None:
            return None
        others = active - self._own_ids()
        requested, self._ignore_requested = self._ignore_requested, False
        if self._ignored is None or requested:
            self._ignored = (self._ignored or set()) | others
        self._ignored &= others   # released the mic: its next use counts
        return bool(others - self._ignored)
```

  In `CallProbe.__init__`, replace `self._probe = probe or other_app_using_mic` with:

```python
        self.watch = MicWatch() if probe is None else None
        self._probe = probe or self.watch
```

  Then add this method:

```python
    def ignore_current(self) -> None:
        if self.watch is not None:
            self.watch.ignore_current()
```

- [ ] **Step 4: `speakeasy/record_prompt.py`.**
  - In `__init__`, add `self._call_waved_off = False   # a call offer got Not Now or timed out`.
  - In `not_now`, and in `_expire` before `_close_offer()`, set `self._call_waved_off = True` when `self.banner.kind == "call"`.
  - Add this method:

```python
    def take_call_waved_off(self) -> bool:
        """True once after a call offer was waved off (Not Now or timeout), so
        the controller can tell the probe to ignore the apps on that call."""
        waved, self._call_waved_off = self._call_waved_off, False
        return waved
```

- [ ] **Step 5: `speakeasy/ui/record_prompt_controller.py`.** At the start of `_render` (after `_persist()`), add:

```python
        if self.coordinator.take_call_waved_off():
            self.probe.ignore_current()
```

- [ ] **Step 6: Docs.**
  - README Record prompts, call banner: only an app that *starts* using the mic counts. Apps already using it when Speakeasy starts are ignored until they release it, as is an app whose call offer you dismissed (or that timed out). So a game launcher or voice app that keeps the mic open only asks once. A call already running when Speakeasy launches isn't offered.
  - AGENTS.md call-probe bullet: `MicWatch` keeps the ignored PIDs inside `call_detect.py`, and the controller only sends `ignore_current()`.

- [ ] **Step 7: Tests, full suite (timeout 300000), commit** `Call detect: only apps that start using the mic count; waved-off calls ignored`.

**Review mutations (Opus):**
- baseline not taken on the first answer;
- `self._ignored &= others` removed (a released app stays ignored);
- `ignore_current` flag never applied;
- `_call_waved_off` set on Record or on a manual meeting start;
- the controller never calls `ignore_current`;
- the expiry path not marking a waved-off call;
- own PIDs not excluded.

- [ ] **Step 8: Reinstall (needs the user's go-ahead) and live check.**
  1. Start GeForce NOW, then relaunch Speakeasy: no call banner.
  2. A synthetic or real call while GeForce NOW stays open: a banner within about 15 s.
  3. Open GeForce NOW while Speakeasy is already running: one banner. Not Now, then a synthetic call within the next minute or two: a banner.

---

## Execution notes

(Record deviations, surprises, review findings and acceptance results here during execution.)

**Status, 2 Oct 2026:** Tasks 1–4 and Task 5 Steps 1–4 are done on branch `record-prompts` (worktree `.claude/worktrees/record-prompts`), HEAD `f0e3c93`, full suite **991 passed**, frontend build OK. Tests only so far: the app has **not** been installed or launched. Next: Step 5 (install, needs the user's go-ahead), Step 6 (live acceptance), Step 7.

Commits: `c0e83f5` call detect, `cc216bd` pin interval/timeout, `1fcd799` settings + toggle, `a2b409e` coordinator, `d15b3c9` panel/controller/wiring, `224d1a7` pin geometry/timings/focus, `cf7c692` docs, `f0e3c93` final-review fixes.

**Per-task reviews (Opus, by mutation):** every listed mutation was caught. Reviewers found Global Constraint values that no test pinned: the 5 s probe, 1.0 s helper timeout, 30 s tick, 2 s confirmation, 360×92 / 12 pt top-right, and `orderFrontRegardless` vs `makeKeyAndOrderFront_`. Tests now pin all of them.

**Whole-branch review found 3 Important issues, fixed in `f0e3c93`:**
- *Stale countdown.* The banner kept "Starts in 2 min" for up to 5 min. The controller now redraws on each tick when the text changes.
- *Call toggle didn't hide call banners* (the spec says it should). Fix: `RecordPromptCoordinator.calls_disabled()`, called from `tick_`.
- *A short mic blip gave a false "Call ended".* This is a **deviation from the plan's code**. `_heard_call` is now set only after 10 s (`CALL_START`) of other-app mic use during a recording. Resumed mic use still hides "Call ended" at once. The existing call-ended tests now observe 10 s of use.

Also in `f0e3c93`:
- Record does nothing unless the engine is ready.
- "N more" ignores out-of-range tags.
- New tests: Keep Recording wiring, 3-invited count, and `set_prompted_events` keeping other settings.
- `menubar.py` catches a `RecordPromptController` init failure, so app launch is not aborted.

**Loose ends (not blocking; fix later):**
- `bannerMore_`'s not-ready guard has no test that catches its removal: the test uses `Item(0)` with one event. Use 2 events and `Item(1)`.
- `menubar.py` uses `logging.exception`, the only `logging` use in `speakeasy/`. The project's pattern is `print(type(err).__name__, file=sys.stderr)`.
- `calls_disabled()` doesn't set `_ended_dismissed`. If detection is switched back on mid-recording, the same "Call ended" can show again.
- Untested minors:
  - bool-PID exclusion;
  - the stop-while-probe-in-flight guard;
  - `eligible_process_ids` `or set()`;
  - the latest-500 test keys sort like plain strings;
  - `pick_event` without `_with_people` on the call path;
  - call-offer expiry via `call_observed`;
  - `_when` rounding at half-minutes.
- First calendar check runs 30 s after launch (still inside the window).
- `test_meeting_recorder::test_gap_fill_queue_full_is_retried_on_the_next_block` is flaky (untouched file, existed before this branch). It failed once in a full run and in 1 of 3 solo reruns.

**Add to Step 6 live acceptance:** check whether an always-on system process (e.g. "Hey Siri") reports running mic input. If it does, it would cause a false "Record this call?" while idle and stop "Call ended" from ever appearing.

**Step 5, 2 Oct 2026:** built and installed from the worktree with `build_app.sh --install` (HEAD `ddeb14a`), then launched `/Applications/Speakeasy.app`. The Claude Desktop connector needs its off/on toggle.

**Step 6, live checks done by Claude (no screen control: screen capture and Accessibility are both denied to the session):**
- **Detection works.** The installed helper's `--list-input-pids` listed a throwaway `sounddevice` recorder within 6 s and dropped it when it stopped. Speakeasy's own processes (main, mic helper) were not listed while idle.
- **Surprise: GeForce NOW (`/Applications/GeForceNOW.app`) keeps Core Audio input running while idle.** Speakeasy therefore showed a call banner about 15 s after launch with no call in progress. This is the "always-on process" risk from Review Focus/M3, and it is real.
  - While GeForce NOW holds the mic, its "call" never ends. Real Teams/Zoom calls get no offer, and "Call ended" never appears.
  - **Open decision for the user:** quit GeForce NOW, or change detection so only apps that *start* using the mic count (per-PID onset tracking kept inside `call_detect.py`).
- **Banner geometry and focus confirmed with CGWindowList.** The banner is 360×92 at x=1356, y=45 (top-left coordinates), which is 12 pt from the top-right of the visible frame (1728×1117 screen, menu bar plus Dock inset). Window layer is 25 (status). The frontmost app stayed NVIDIA GeForce NOW, so the banner took no focus.
- **Check 5 passed, and the I2 fix works live.** Setting `detect_calls: false` in settings.json hid the showing call banner within 5 s. No banner came back for 45 s while GeForce NOW still held the mic. settings.json was then restored byte for byte from a backup; only that key had changed.
- **One offer per call holds across the toggle.** After detection was switched back on with GeForce NOW still using the mic, no new banner appeared.
- **Not checked yet (needs the user's eyes or clicks):**
  - (1) the toggles in Meetings › Settings, and that they persist across relaunch;
  - (2) a real calendar meeting banner and its Record button;
  - (3) the Teams call banner, and Not Now (blocked by GeForce NOW, see above);
  - (4) "Call ended" → Stop / Keep Recording;
  - (6) dictation paste in TextEdit, Codex and Teams;
  - the banner's text and look.
- **After GeForce NOW was quit (same day):**
  - **Synthetic call** (a throwaway `sounddevice` recorder held the mic for 30 s after 65 s idle): the banner appeared **11.4 s** after mic start. Microsoft Teams stayed frontmost. The banner linked to the user's calendar event that started at 12:00 and was still in progress.
  - **The user clicked Not Now** about 46 s after the mic was released. That event was then saved as prompted for the day (`record_prompted`, 1 key).
  - **Check 3 passed:** the call was detected, linked to the right event, the banner took no focus, and Not Now was remembered.
  - **Not verified live:** the call offer hiding by itself about 60 s after the call ends, because the click came first. Unit tests cover it (`test_call_offer_hides_when_the_call_ends`).
  - **Still for the user:** checks 1, 2, 4 and 6.
- **User, 2 Oct:** toggled the call-offer setting in Meetings › Settings (persistence after relaunch not yet confirmed). Chose the follow-up "only apps that start using the mic count" → Task 6.
