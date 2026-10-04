# Recording Pill, Menu-bar Next-up and Today Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Dock window with a floating native recording pill (with Notes, Stop and a capture drawer), a native menu-bar menu with a Next-up card, and a focused Today home in the Meetings window.

**Architecture:** Pure-Python models (`pill_model.py`, `next_up.py`, `menu_model.py`, `meeting_options.default_options`) carry every decision and are unit-tested without AppKit; thin AppKit layers (`pill_panel.py`, `pill_controller.py`, `next_up_view.py`) draw them, following the record-prompt banner's controller/panel split. The Meetings page gains a navigation hand-off (`meetings.takeNavigation`), a focus-layout Today (`agendaSplit.ts`) and two Settings switches. The Dock window (`ui/main_window.py`, `frontend/src/dock/`) is deleted last. `engine.py` is not changed.

**Tech Stack:** Python 3.11 + PyObjC (AppKit), React 18 + TypeScript + Vite, pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-03-recording-pill-design.md` (agreed 3 Oct 2026). The Quiet Library UI rules bind: `docs/superpowers/specs/2026-10-02-quiet-library-design.md`.

## Execution roles (user requirement — binding)

| Role | Model | Agent `model` value | Covers |
|---|---|---|---|
| Implementer | Sonnet 5.5 (`claude-sonnet-5-5`) | `"sonnet"` | Every task's Steps, including every fix after a review |
| Task reviewer | Opus 5.5 (`claude-opus-5-5`) | `"opus"` | Spec-compliance and quality review after each task, with mutation checks |
| Final whole-branch review | Opus 5.5 | `"opus"` | One review of the complete branch before merge |
| Controller | The session model (Opus) | — | Dispatches, records, merges; writes no feature code |

- **Set `model` explicitly on every Agent dispatch.**
- If a Sonnet implementer is stuck after two attempts, re-dispatch on Sonnet with a sharper brief; never switch implementation to Opus without asking the user.
- Reviewers verify by **mutation**: break the code, confirm a test fails, restore with `git checkout`, then `find speakeasy tests -name __pycache__ -type d -prune -exec rm -rf {} +`. Python mutation runs: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider <file>`. A review with no mutation is not a pass.
- Record the model used for each dispatch in Execution notes.
- Long Bash calls (full pytest ≈ 2–3 min, builds) get `timeout: 600000`; say so in every brief. Never run app code on the real `~/Library/Application Support/Speakeasy`: pytest's conftest isolates HOME; real-app checks use a temp HOME via Python `subprocess.run(env={"HOME": tmp, ...})`.

## Usage-limit handoff (user requirement)

When a usage-limit notice arrives or the session must stop before the plan is finished: finish only the piece in hand; append a dated **Progress** block to Execution notes (tasks done with commit ranges, task in progress, open findings, rulings) and commit it on the branch; stop anything running; end with `Safe to /clear: yes` (or what is missing) and a paste-ready prompt, e.g. `Execute docs/superpowers/plans/2026-10-03-recording-pill.md, subagent-driven, resuming at Task N in worktree .claude/worktrees/recording-pill (ledger .superpowers/sdd/2026-10-03-recording-pill/progress.md).`

## Setup (once, before Task 1)

- [ ] `git worktree add .claude/worktrees/recording-pill -b recording-pill master`; in it `ln -s ../../../.venv .venv`, `ln -s ../../../models models`, `npm --prefix frontend ci` (the main checkout's `node_modules` was installed on 3 Oct, but a worktree needs its own; check `frontend/node_modules/.bin/tsc` exists afterwards).
- [ ] Baseline: `npm --prefix frontend run build`, `npm --prefix frontend test`, `.venv/bin/python -m pytest -q` (≈ 1065 passed; `timeout: 600000`). Record the counts in Execution notes.

## Plan-time decisions (where the plan departs from or sharpens the spec)

- Navigation hand-off: the controller stores one pending request on the bridge and emits `meetings.navigate` **without payload**; the page always calls `meetings.takeNavigation` (on load and on the event), so a request is applied exactly once whether or not the page had loaded.
- The pill's processing → done transitions live in a pure `PillMachine` (in `pill_model.py`). Engine order is fixed: `on_meeting_saved` fires before the state returns to idle; a cancel fires no saved event; a failure sets `engine.meeting_processing_error` before the state change.
- A save that lands while the user is confirming or cancelling shows **Saved** (the meeting exists).
- `agenda_row` gains `start` and `end` (local ISO 8601 with offset) so Today can split the day by real times instead of parsing "9:00 AM".
- Start at Login moves to Settings › General through a new `speakeasy/ui/login_item.py` (the `SMAppService` code moves out of `menubar.py` unchanged).
- The menu's custom Next-up row is refreshed in `menuWillOpen_` (the menu's delegate), so it never runs a library query on a timer.
- `check_quit_and_cancel.py` keeps working by driving cancel through `PillController.pillDiscard_` instead of the deleted Dock bridge.

## Global Constraints

- Fully offline; no new dependencies (Python or npm).
- Do not edit `speakeasy/engine.py`, `speakeasy/recorder.py`, `speakeasy/meeting_recorder.py` or any dictation/insertion code. The insertion and wake-recovery rules in `AGENTS.md` are untouched.
- Capture health: read only the existing `MeetingHealth.to_dict()` keys and `system_audio_status`; never show or log app names, device names, PIDs, window titles or transcript text.
- Pill panel: borderless, `NSWindowStyleMaskNonactivatingPanel`, `NSStatusWindowLevel`, collection behaviour `CanJoinAllSpaces | Stationary | FullScreenAuxiliary`, `setBecomesKeyOnlyIfNeeded_(True)`, `setHidesOnDeactivate_(False)`, buttons are `glass.ClickyButton`. Size **360 × 36 pt**, corner radius 18, material `NSVisualEffectMaterialHUDWindow`. Default origin: horizontally centred on the screen under the mouse, top edge 8 pt below that screen's `visibleFrame` top.
- Drawer panel: same window flags, **360 pt** wide (the pill's width), 6 pt below the pill, left-aligned with it (same x), corner radius 12, material `NSVisualEffectMaterialPopover`.
- Colours (native): recording dot and Stop use the banner's `STOP_RED`; Record uses `CORAL`; warnings `NSColor.systemOrangeColor()`; OK indicator `NSColor.systemGreenColor()`; waiting indicator `NSColor.tertiaryLabelColor()`. Web CSS uses only `tokens.css` tokens (`tests/test_frontend_tokens.py` lints new modules); coral (`--accent`) only for Record actions.
- Timers: `mm:ss` under an hour, `h:mm:ss` from one hour. Saved state lasts **6 s**. Audio indicator turns amber when there is still no signal **10 s** after the recording started.
- Today: hero card + **3** next events; earlier events folded; "Show all N events" when more than 3 later events.
- Copy strings, verbatim: "Untitled meeting", "Notes", "Processing", "Cancel", "Discard meeting?", "Discard", "Keep", "Cancelling…", "Saved", "Open", "Couldn't finish", "Open Meetings", "Link to event…", "Unlink", "Paused until the meeting ends", "Audio stays on this Mac and is deleted once the transcript is saved.", "Start Meeting", "End Meeting", "Open notes", "Now", "Nothing else today", "Show all {n} events", "Show fewer", "Earlier today · {n} meetings, {m} recorded", "Identify enrolled voices", "Start at login", "Meeting Notes", "Open Meetings", "Train My Voice…", "Check Microphone…", "Settings…", "Retry Microphone".
- Every UI start path builds its options with `meeting_options.default_options()`.

## Review Focus

1. **The pill steals focus from the call app** (typing in Teams/Zoom stops when the pill appears or is clicked). Expected: focus never moves. Test: Task 4 `test_pill_panel_is_non_activating_and_forwards_clicks` (same assertions as the banner's panel test) and that showing it does not call `makeKeyWindow`.
2. **Saved pill position is on a display that has since been unplugged.** Expected: the pill appears at the default spot. Test: Task 3 `test_clamp_origin_rejects_offscreen` and Task 5 `test_saved_origin_offscreen_uses_default`.
3. **Recording started from the banner, menu, Today or Next-up card** all show the pill. Expected: the pill follows engine state, not the start path. Test: Task 5 `test_pill_shows_on_any_meeting_recording_state`.
4. **The user presses Cancel → Discard just as the save completes.** Expected: "Saved · Open" (the meeting exists), never a silent disappearance. Test: Task 3 `test_saved_during_cancel_shows_saved`.
5. **Notes is clicked before the Meetings window has ever opened** (window created by the click, page not loaded). Expected: the page opens on Recording now. Test: Task 2 `test_take_navigation_returns_once` + `navigationAction` node tests; Task 9 data-level check.

---

### Task 1: Default meeting options and the two new settings

**Files:**
- Modify: `speakeasy/settings.py` (add after `set_appearance`)
- Modify: `speakeasy/meeting_options.py` (append `default_options`)
- Create: `speakeasy/ui/login_item.py`
- Modify: `speakeasy/ui/menubar.py` (`toggleMeeting_`, `_sync_login_item`, `toggleLogin_`, remove `_login_service`)
- Modify: `speakeasy/ui/record_prompt_controller.py:171`
- Modify: `speakeasy/ui/meetings_bridge.py` (constructor kwargs, `calendar_record_payload`, new `meeting.start`, settings payloads)
- Modify: `speakeasy/ui/meetings_window.py` (pass `login_status`, `set_login`)
- Modify: `frontend/src/mock/meetings.ts` (`MeetingSettings`), `frontend/src/meetings/SettingsSheet.tsx`, `frontend/src/meetings/App.tsx` (onChange patch type)
- Test: `tests/test_settings.py`, `tests/test_meeting_options.py` (create), `tests/test_meetings_bridge.py`

**Interfaces:**
- Produces: `settings.get_identify_voices() -> bool` (default `False`); `settings.set_identify_voices(value) -> bool` (ValueError `"Identify voices must be on or off."`); `settings.get_pill_origin() -> tuple[float, float] | None`; `settings.set_pill_origin(x: float, y: float) -> None` (ValueError `"Pill position must be two numbers."` for non-finite/non-number); `meeting_options.default_options(calendar_event_key: str | None = None, *, identify_voices: bool | None = None, voice_names=None) -> MeetingOptions`; `login_item.status() -> bool | None` (None = unavailable); `login_item.set_enabled(enabled: bool) -> None` (raises `RuntimeError(message)`); bridge methods `meeting.start` → `True`; settings payload keys `identifyVoices: bool`, `startAtLogin: bool | null`.

- [ ] **Step 1: Write the failing tests**

`tests/test_meeting_options.py`:
```python
import pytest

from speakeasy import settings
from speakeasy.meeting_options import MeetingOptions, default_options


def test_defaults_are_all_system_audio_and_auto_speakers():
    assert default_options(identify_voices=False) == MeetingOptions()


def test_event_key_is_passed_through():
    assert default_options("ev-1", identify_voices=False) == MeetingOptions(calendar_event_key="ev-1")


def test_identify_voices_uses_every_enrolled_voice():
    opts = default_options(identify_voices=True, voice_names=["Ana", "Bo"])
    assert opts.expected_voice_profile_names == ("Ana", "Bo")
    assert opts.system_audio_pid is None and opts.expected_speaker_count is None


def test_identify_voices_off_ignores_enrolled_voices():
    assert default_options(identify_voices=False, voice_names=["Ana"]).expected_voice_profile_names == ()


def test_reads_the_setting_when_not_given(monkeypatch):
    settings.set_identify_voices(True)
    import speakeasy.voice_profiles as vp
    monkeypatch.setattr(vp.VoiceProfileStore, "names", lambda self: ["Cy"])
    assert default_options().expected_voice_profile_names == ("Cy",)
```

Append to `tests/test_settings.py`:
```python
import math


def test_identify_voices_defaults_off_and_round_trips():
    assert settings.get_identify_voices() is False
    assert settings.set_identify_voices(True) is True
    assert settings.get_identify_voices() is True


def test_identify_voices_rejects_non_bool():
    with pytest.raises(ValueError, match="Identify voices must be on or off."):
        settings.set_identify_voices("yes")


def test_pill_origin_round_trips_and_rejects_bad_values():
    assert settings.get_pill_origin() is None
    settings.set_pill_origin(120.5, 800.0)
    assert settings.get_pill_origin() == (120.5, 800.0)
    for bad in ((math.nan, 1.0), (1.0, math.inf), ("1", 2.0), (True, 2.0)):
        with pytest.raises(ValueError, match="Pill position must be two numbers."):
            settings.set_pill_origin(*bad)


def test_corrupt_pill_origin_reads_as_none():
    data = settings._read()
    data["pill_origin"] = ["x", 3]
    settings._write(data)
    assert settings.get_pill_origin() is None
```
(If `tests/test_settings.py` lacks `import pytest`, add it.)

Append to `tests/test_meetings_bridge.py` (use the file's existing bridge-construction helper; if it builds `MeetingsBridge(...)` directly, pass the new kwargs the same way):
```python
def test_meeting_start_uses_default_options(tmp_path):
    started = []
    bridge = make_bridge(tmp_path, begin_meeting=started.append)
    assert bridge.start_meeting_payload({}) is True
    assert started == [MeetingOptions()]


def test_calendar_record_uses_default_options(tmp_path):
    started = []
    bridge = make_bridge(tmp_path, begin_meeting=started.append)
    bridge.calendar_record_payload({"key": "ev-9"})
    assert started == [MeetingOptions(calendar_event_key="ev-9")]


def test_settings_include_identify_voices_and_login(tmp_path):
    state = {"login": False}
    bridge = make_bridge(tmp_path, login_status=lambda: state["login"],
                         set_login=lambda on: state.update(login=on))
    got = bridge.settings_get_payload({})
    assert got["identifyVoices"] is False and got["startAtLogin"] is False
    got = bridge.settings_set_payload({"identifyVoices": True, "startAtLogin": True})
    assert got["identifyVoices"] is True and got["startAtLogin"] is True


def test_login_unavailable_reads_null_and_refuses_set(tmp_path):
    bridge = make_bridge(tmp_path)
    assert bridge.settings_get_payload({})["startAtLogin"] is None
    with pytest.raises(ValueError, match="Start at login is available in the installed app."):
        bridge.settings_set_payload({"startAtLogin": True})
```
`make_bridge` is the module's existing factory; if none exists, add one at the top of the new tests: `def make_bridge(tmp_path, **kw): return MeetingsBridge(library=MeetingLibrary(tmp_path / "l.sqlite"), **kw)` matching the constructor's real first parameters (read the top of the test file first).

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_meeting_options.py tests/test_settings.py tests/test_meetings_bridge.py`
Expected: FAIL (`default_options`, `get_identify_voices`, `start_meeting_payload` missing).

- [ ] **Step 3: Implement**

`speakeasy/settings.py`, after the appearance section:
```python
# -- recording ------------------------------------------------------------------


def get_identify_voices() -> bool:
    value = _read().get("identify_voices")
    return value if isinstance(value, bool) else False


def set_identify_voices(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Identify voices must be on or off.")
    data = _read()
    data["identify_voices"] = value
    _write(data)
    return value


def get_pill_origin() -> tuple[float, float] | None:
    raw = _read().get("pill_origin")
    if (isinstance(raw, list) and len(raw) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) for v in raw)):
        return (float(raw[0]), float(raw[1]))
    return None


def set_pill_origin(x, y) -> None:
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) for v in (x, y)):
        raise ValueError("Pill position must be two numbers.")
    data = _read()
    data["pill_origin"] = [float(x), float(y)]
    _write(data)
```
Add `import math` at the top of `settings.py` if absent.

`speakeasy/meeting_options.py`, append:
```python
def default_options(calendar_event_key: str | None = None, *,
                    identify_voices: bool | None = None,
                    voice_names=None) -> MeetingOptions:
    """Options for every UI start: all system audio, speakers Auto, and the
    enrolled voices only when Settings › Identify enrolled voices is on."""
    if identify_voices is None:
        from . import settings
        identify_voices = settings.get_identify_voices()
    names: tuple[str, ...] = ()
    if identify_voices:
        if voice_names is None:
            from .voice_profiles import VoiceProfileStore
            voice_names = VoiceProfileStore().names()
        names = tuple(str(n) for n in voice_names)
    return MeetingOptions(expected_voice_profile_names=names,
                          calendar_event_key=calendar_event_key)
```

`speakeasy/ui/login_item.py` — move `_login_service` from `menubar.py` verbatim as `_service()`, then:
```python
"""Launch-at-login via SMAppService (installed .app, macOS 13+ only)."""

import sys

import objc


def _service():
    ...  # body of menubar._login_service, unchanged


def status() -> bool | None:
    service = _service()
    if service is None:
        return None
    return service.status() == 1  # SMAppServiceStatusEnabled


def set_enabled(enabled: bool) -> None:
    service = _service()
    if service is None:
        raise RuntimeError("Start at login is available in the installed app.")
    if enabled == (service.status() == 1):
        return
    if enabled:
        ok, err = service.registerAndReturnError_(None)
    else:
        ok, err = service.unregisterAndReturnError_(None)
    if not ok:
        raise RuntimeError(str(err))
```
In `menubar.py`: delete `_login_service`; `_sync_login_item`/`toggleLogin_` call `login_item.status()` / `login_item.set_enabled(not current)` (they are removed entirely in Task 7; keep them working now). `toggleMeeting_`: `self.engine.begin_meeting(default_options())` (import from `..meeting_options`).

`record_prompt_controller.py:171`: `self.engine.begin_meeting(default_options(calendar_event_key=key))` (import `default_options` instead of `MeetingOptions` if that is its only use).

`meetings_bridge.py`: constructor gains `login_status=None, set_login=None` stored as `self._login_status`, `self._set_login`; `calendar_record_payload` calls `self._begin_meeting(default_options(calendar_event_key=key))`; register `"meeting.start": self.start_meeting_payload`:
```python
    def start_meeting_payload(self, params) -> bool:
        if self._begin_meeting is None:
            raise BridgeError("recording_unavailable")
        self._begin_meeting(default_options())
        return True
```
`settings_get_payload` adds `"identifyVoices": settings.get_identify_voices()` and `"startAtLogin": self._login_status() if self._login_status else None`. `settings_set_payload` first handles:
```python
        if params.get("identifyVoices") is not None:
            settings.set_identify_voices(params["identifyVoices"])
        if params.get("startAtLogin") is not None:
            if not isinstance(params["startAtLogin"], bool):
                raise ValueError("Start at login must be on or off.")
            if self._set_login is None or self._login_status is None or self._login_status() is None:
                raise ValueError("Start at login is available in the installed app.")
            try:
                self._set_login(params["startAtLogin"])
            except RuntimeError as err:
                raise ValueError(str(err)) from err
```
`meetings_window.py`: pass `login_status=login_item.status, set_login=login_item.set_enabled`.

Frontend: `MeetingSettings` gains `identifyVoices: boolean; startAtLogin: boolean | null;` (update the mock settings object with `identifyVoices: false, startAtLogin: false`). `SettingsSheet` `onChange` patch type gains `identifyVoices?: boolean; startAtLogin?: boolean`. Add, after the Appearance section and before Calendars:
```tsx
      <div className={styles.sectionTitle}>Recording</div>
      <div className={styles.toggleRow}>
        <span id="identify-voices-label">
          Identify enrolled voices
          <span className={styles.hint}>Names people whose voices you've trained</span>
        </span>
        <Switch checked={identifyVoices} onChange={(value) => onChange({ identifyVoices: value })} ariaLabelledBy="identify-voices-label" />
      </div>

      <div className={styles.sectionTitle}>General</div>
      <div className={styles.toggleRow}>
        <span id="start-at-login-label">
          Start at login
          {startAtLogin === null && <span className={styles.hint}>Available in the installed app</span>}
        </span>
        <Switch checked={startAtLogin === true} disabled={startAtLogin === null} onChange={(value) => onChange({ startAtLogin: value })} ariaLabelledBy="start-at-login-label" />
      </div>
```
If `Switch` has no `disabled` prop, add one (`disabled?: boolean` → sets the button's `disabled` and `aria-disabled`). Update the App's `onChange` handler type to pass the new keys through to `settings.meetings.set` unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_meeting_options.py tests/test_settings.py tests/test_meetings_bridge.py tests/test_record_prompt_controller.py` then `npm --prefix frontend run build`
Expected: PASS; build OK.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/settings.py speakeasy/meeting_options.py speakeasy/ui/login_item.py speakeasy/ui/menubar.py speakeasy/ui/record_prompt_controller.py speakeasy/ui/meetings_bridge.py speakeasy/ui/meetings_window.py frontend/src tests
git commit -m "Default meeting options, identify-voices and start-at-login settings"
```

---

### Task 2: Open Meetings on a page (navigation hand-off)

**Files:**
- Modify: `speakeasy/ui/meetings_bridge.py` (pending navigation, `meetings.takeNavigation`)
- Modify: `speakeasy/ui/meetings_window.py` (`show(view=None)`)
- Modify: `speakeasy/ui/menubar.py` (`open_meetings`, `openMeetings_`)
- Create: `frontend/src/meetings/navigation.ts`, `frontend/tests/navigation.test.ts`
- Modify: `frontend/src/meetings/App.tsx`
- Test: `tests/test_meetings_bridge.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `MeetingsBridge.set_navigation(view)` where `view` is `"recording"`, `"settings"` or `{"meeting": str}` (ValueError `"Unknown page."` otherwise; meeting id 1–200 chars); bridge method `meetings.takeNavigation` → the pending view or `None`, clearing it; `MeetingsWindowController.show(view=None)`; `StatusItemController.open_meetings(view=None)` (python method — the single way every surface opens Meetings); page helper `navigationAction(nav, recording) -> {kind: 'recording'|'today'|'settings'|'meeting'|'none', id?: string}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_meetings_bridge.py`:
```python
def test_take_navigation_returns_once(tmp_path):
    bridge = make_bridge(tmp_path)
    assert bridge.take_navigation_payload({}) is None
    bridge.set_navigation("recording")
    assert bridge.take_navigation_payload({}) == "recording"
    assert bridge.take_navigation_payload({}) is None


def test_latest_navigation_wins(tmp_path):
    bridge = make_bridge(tmp_path)
    bridge.set_navigation("settings")
    bridge.set_navigation({"meeting": "m-1"})
    assert bridge.take_navigation_payload({}) == {"meeting": "m-1"}


@pytest.mark.parametrize("bad", ["today", {"meeting": ""}, {"meeting": "x" * 201}, {"other": "m"}, 3])
def test_unknown_navigation_is_refused(tmp_path, bad):
    with pytest.raises(ValueError, match="Unknown page."):
        make_bridge(tmp_path).set_navigation(bad)
```

`frontend/tests/navigation.test.ts`:
```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { navigationAction } from '../src/meetings/navigation.ts';

const idle = { recording: false, processing: false, startedAt: null, title: null };
const live = { ...idle, recording: true };

test('recording opens Recording now while a meeting records or processes', () => {
  assert.deepEqual(navigationAction('recording', live), { kind: 'recording' });
  assert.deepEqual(navigationAction('recording', { ...idle, processing: true }), { kind: 'recording' });
});

test('recording falls back to Today once the meeting is over', () => {
  assert.deepEqual(navigationAction('recording', idle), { kind: 'today' });
});

test('settings and meeting map through; anything else is ignored', () => {
  assert.deepEqual(navigationAction('settings', idle), { kind: 'settings' });
  assert.deepEqual(navigationAction({ meeting: 'm-1' }, idle), { kind: 'meeting', id: 'm-1' });
  assert.deepEqual(navigationAction(null, idle), { kind: 'none' });
  assert.deepEqual(navigationAction({ meeting: '' } as never, idle), { kind: 'none' });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_meetings_bridge.py -k navigation` and `npm --prefix frontend test`
Expected: FAIL (missing methods/module).

- [ ] **Step 3: Implement**

`meetings_bridge.py`: `self._navigation = None` in `__init__`; register `"meetings.takeNavigation": self.take_navigation_payload`:
```python
    def set_navigation(self, view) -> None:
        ok = view in ("recording", "settings") or (
            isinstance(view, dict) and set(view) == {"meeting"}
            and isinstance(view["meeting"], str) and 0 < len(view["meeting"]) <= 200)
        if not ok:
            raise ValueError("Unknown page.")
        self._navigation = view

    def take_navigation_payload(self, params):
        view, self._navigation = self._navigation, None
        return view
```
`meetings_window.py`:
```python
    def show(self, view=None):
        if view is not None:
            self._bridge.set_navigation(view)
        self._bridge.poll_changed()
        self._web.emit("meetings.changed")
        if view is not None:
            self._web.emit("meetings.navigate")
        self._web.show()
        ...  # existing poll-timer code unchanged
```
`menubar.py` `StatusItemController`:
```python
    @objc.python_method
    def open_meetings(self, view=None):
        from .meetings_window import MeetingsWindowController

        if self.meetings_window is None:
            self.meetings_window = MeetingsWindowController.alloc().init()
            if self._pending_library_status is not None:
                self.meetings_window.libraryStatus_(self._pending_library_status)
        self.meetings_window.show(view)

    def openMeetings_(self, sender):
        self.open_meetings(None)
```
`frontend/src/meetings/navigation.ts` (import-free apart from types):
```ts
import type { RecordingInfo } from '../mock/meetings';

export type Navigation = 'recording' | 'settings' | { meeting: string } | null;
export type NavigationAction =
  | { kind: 'recording' } | { kind: 'today' } | { kind: 'settings' }
  | { kind: 'meeting'; id: string } | { kind: 'none' };

export function navigationAction(nav: Navigation, recording: RecordingInfo): NavigationAction {
  if (nav === 'recording') return recording.recording || recording.processing ? { kind: 'recording' } : { kind: 'today' };
  if (nav === 'settings') return { kind: 'settings' };
  if (nav && typeof nav === 'object' && typeof nav.meeting === 'string' && nav.meeting) return { kind: 'meeting', id: nav.meeting };
  return { kind: 'none' };
}
```
`App.tsx` — one effect, embedded only:
```tsx
  useEffect(() => {
    if (!embedded) return;
    const apply = () => {
      Promise.all([
        bridge.call<Navigation>('meetings.takeNavigation'),
        bridge.call<RecordingInfo>('recording.get'),
      ])
        .then(([nav, rec]) => {
          const action = navigationAction(nav, rec);
          if (action.kind === 'recording') { setRecording(rec); onSelectRecording(); }
          else if (action.kind === 'today') onSelectToday();
          else if (action.kind === 'settings') setSettingsOpen(true);
          else if (action.kind === 'meeting') { userNavigatedRef.current = true; setRecordingView(false); setToday(false); setSearching(false); select(action.id); }
        })
        .catch((err) => console.error('navigation failed', err));
    };
    apply();
    return bridge.on('meetings.navigate', apply);
  }, [embedded]);
```
Use refs for `onSelectRecording`/`onSelectToday`/`select` if the linter or stale closures require it (follow the existing `onMeetingSavedRef` pattern). If the Settings sheet needs `meetingSettings` loaded first, open it after `settings.meetings.get` resolves, as the sidebar's Settings button does.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q tests/test_meetings_bridge.py`, `npm --prefix frontend test`, `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/ui/meetings_bridge.py speakeasy/ui/meetings_window.py speakeasy/ui/menubar.py frontend tests
git commit -m "Open Meetings on Recording now, Settings or a meeting"
```

---

### Task 3: Pill model (pure)

**Files:**
- Create: `speakeasy/ui/pill_model.py`
- Test: `tests/test_pill_model.py`

**Interfaces:**
- Produces:
  - `format_timer(seconds: float) -> str`
  - `audio_indicator(health: dict, capture_mode: str, elapsed: float) -> str` — `"waiting" | "ok" | "warning"`
  - `drawer_rows(health: dict, capture_mode: str, system_audio_status: str, elapsed: float, event_title: str | None) -> list[DrawerRow]`, `DrawerRow(label: str, value: str, tone: str)` with tone `"ok" | "warning" | "muted" | "action"`
  - `PillView(phase, title, detail, indicator, buttons)` frozen dataclass
  - `PillMachine` with `.phase`, `.saved_id`, `.on_state(state: str, processing_error: str | None)`, `.on_saved(meeting_id: str)`, `.cancel()`, `.keep()`, `.discard()`, `.expire_saved()`, `.close()`
  - `pill_view(machine, *, title, elapsed, health, capture_mode, progress) -> PillView`
  - `clamp_origin(origin, size, screens) -> tuple | None`; `default_origin(screen, size) -> tuple`; screens and screen as `(x, y, w, h)` tuples (visible frames)
  - Phases: `"hidden"`, `"recording"`, `"processing"`, `"confirm_discard"`, `"cancelling"`, `"saved"`, `"failed"`. Buttons (tuple of ids): recording `("notes", "stop")`; processing `("notes", "cancel")`; confirm_discard `("discard", "keep")`; cancelling `()`; saved `("open",)`; failed `("open_meetings", "close")`.

- [ ] **Step 1: Write the failing tests** — `tests/test_pill_model.py`:
```python
import pytest

from speakeasy.ui.pill_model import (
    PillMachine, audio_indicator, clamp_origin, default_origin, drawer_rows,
    format_timer, pill_view)

OK = {"mic_first_buffer": True, "system_first_buffer": True, "system_nonzero_signal": True}


@pytest.mark.parametrize("s,text", [(0, "00:00"), (59.9, "00:59"), (723, "12:03"),
                                    (3600, "1:00:00"), (3723, "1:02:03")])
def test_format_timer(s, text):
    assert format_timer(s) == text


def test_indicator_ok_when_both_tracks_have_signal():
    assert audio_indicator(OK, "mic_and_system", 30) == "ok"


def test_indicator_waits_then_warns_without_signal():
    quiet = {**OK, "system_nonzero_signal": False}
    assert audio_indicator(quiet, "mic_and_system", 9.9) == "waiting"
    assert audio_indicator(quiet, "mic_and_system", 10) == "warning"
    assert audio_indicator({}, "mic_and_system", 2) == "waiting"
    assert audio_indicator({}, "mic_and_system", 12) == "warning"


@pytest.mark.parametrize("bad", [{"helper_exited": True, "helper_exit_reason": "crash"},
                                 {"system_writer_failed": True}, {"mic_writer_failed": True},
                                 {"mic_writer_lagged": True}, {"system_writer_lagged": True}])
def test_indicator_warns_on_failures(bad):
    assert audio_indicator({**OK, **bad}, "mic_and_system", 30) == "warning"


def test_requested_stop_is_not_a_failure():
    assert audio_indicator({**OK, "helper_exited": True, "helper_exit_reason": "requested_stop"},
                           "mic_and_system", 30) == "ok"


def test_mic_only_fallback_warns():
    assert audio_indicator({"mic_first_buffer": True}, "mic_only", 30) == "warning"


def test_drawer_rows_working():
    rows = drawer_rows(OK, "mic_and_system", "available", 30, "Weekly 1:1")
    assert [(r.label, r.value, r.tone) for r in rows] == [
        ("Event", "Weekly 1:1", "action"),
        ("Microphone", "You · working", "ok"),
        ("System audio", "All apps · working", "ok"),
        ("Dictation", "Paused until the meeting ends", "muted")]


def test_drawer_rows_unlinked_and_problems():
    rows = drawer_rows({"mic_first_buffer": True}, "mic_only", "permission_denied_or_unavailable", 30, None)
    assert rows[0].value == "Link to event…"
    assert rows[2].tone == "warning"
    assert "System Settings" in rows[2].value


def test_drawer_rows_never_mention_apps_or_pids():
    rows = drawer_rows({**OK, "capture_scope": "selected"}, "mic_and_system", "available", 30, None)
    assert all("PID" not in r.value for r in rows)


def m(*steps):
    machine = PillMachine()
    for step, *args in steps:
        getattr(machine, step)(*args)
    return machine


def test_recording_then_saved_then_expiry():
    machine = m(("on_state", "meeting_recording", None), ("on_state", "meeting_processing", None))
    assert machine.phase == "processing"
    machine.on_saved("m-1")
    machine.on_state("ready", None)
    assert (machine.phase, machine.saved_id) == ("saved", "m-1")
    machine.expire_saved()
    assert machine.phase == "hidden"


def test_cancel_confirm_keep_returns_to_processing():
    machine = m(("on_state", "meeting_processing", None), ("cancel",))
    assert machine.phase == "confirm_discard"
    machine.keep()
    assert machine.phase == "processing"


def test_discard_then_idle_hides():
    machine = m(("on_state", "meeting_processing", None), ("cancel",), ("discard",))
    assert machine.phase == "cancelling"
    machine.on_state("meeting_processing", None)
    assert machine.phase == "cancelling"
    machine.on_state("ready", None)
    assert machine.phase == "hidden"


def test_saved_during_cancel_shows_saved():
    machine = m(("on_state", "meeting_processing", None), ("cancel",), ("discard",), ("on_saved", "m-2"))
    machine.on_state("ready", None)
    assert (machine.phase, machine.saved_id) == ("saved", "m-2")


def test_processing_error_shows_failed_until_closed():
    machine = m(("on_state", "meeting_processing", None), ("on_state", "ready", "processing_failed"))
    assert machine.phase == "failed"
    machine.close()
    assert machine.phase == "hidden"


def test_cleanup_error_after_save_keeps_saved():
    machine = m(("on_state", "meeting_processing", None), ("on_saved", "m-3"),
                ("on_state", "ready", "cleanup_failed"))
    assert machine.phase == "saved"


def test_new_recording_replaces_saved():
    machine = m(("on_state", "meeting_processing", None), ("on_saved", "m-4"), ("on_state", "ready", None),
                ("on_state", "meeting_recording", None))
    assert (machine.phase, machine.saved_id) == ("recording", None)


def test_idle_without_meeting_stays_hidden():
    assert m(("on_state", "ready", None), ("on_state", "mic_failed", None)).phase == "hidden"


def test_pill_view_recording_and_processing():
    machine = m(("on_state", "meeting_recording", None))
    view = pill_view(machine, title=None, elapsed=723, health=OK, capture_mode="mic_and_system", progress=None)
    assert (view.title, view.detail, view.indicator, view.buttons) == (
        "Untitled meeting", "12:03", "ok", ("notes", "stop"))
    machine.on_state("meeting_processing", None)
    view = pill_view(machine, title="Weekly", elapsed=0, health={}, capture_mode="mic_only",
                     progress="Transcribing meeting… 42%")
    assert (view.title, view.detail, view.buttons) == ("Processing", "Transcribing meeting… 42%", ("notes", "cancel"))


def test_pill_view_other_phases():
    machine = m(("on_state", "meeting_processing", None), ("cancel",))
    assert pill_view(machine, title=None, elapsed=0, health={}, capture_mode="", progress=None).title == "Discard meeting?"
    machine.discard()
    assert pill_view(machine, title=None, elapsed=0, health={}, capture_mode="", progress=None).title == "Cancelling…"


def test_clamp_origin_accepts_fully_visible():
    assert clamp_origin((100, 800), (360, 36), [(0, 0, 1440, 875)]) == (100, 800)


def test_clamp_origin_rejects_offscreen():
    assert clamp_origin((2000, 800), (360, 36), [(0, 0, 1440, 875)]) is None
    assert clamp_origin((1200, 800), (360, 36), [(0, 0, 1440, 875)]) is None  # straddles the edge


def test_default_origin_is_top_centre():
    assert default_origin((0, 0, 1440, 875), (360, 36)) == (540.0, 831.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_pill_model.py`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement** — `speakeasy/ui/pill_model.py`:
```python
"""What the recording pill and its capture drawer show. Pure: no AppKit, so
every rule is unit-tested; pill_panel.py draws the result."""

from dataclasses import dataclass

SIGNAL_GRACE_SECONDS = 10.0
UNTITLED = "Untitled meeting"
_SYSTEM_FIXES = {
    "requires_macos_14_2": "Unavailable — needs macOS 14.2 or later",
    "permission_denied_or_unavailable":
        "Off — allow Speakeasy in System Settings › Privacy & Security › Screen & System Audio Recording",
    "helper_missing": "Unavailable — reinstall Speakeasy",
    "helper_exited": "Stopped — microphone only for the rest of this meeting",
}


def format_timer(seconds: float) -> str:
    total = max(0, int(seconds))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _failed(health: dict) -> bool:
    exited = health.get("helper_exited") and health.get("helper_exit_reason") != "requested_stop"
    return bool(exited or health.get("system_writer_failed") or health.get("mic_writer_failed"))


def audio_indicator(health: dict, capture_mode: str, elapsed: float) -> str:
    if _failed(health) or health.get("mic_writer_lagged") or health.get("system_writer_lagged"):
        return "warning"
    if health and capture_mode != "mic_and_system":
        return "warning"
    arriving = (health.get("mic_first_buffer") and health.get("system_first_buffer")
                and health.get("system_nonzero_signal"))
    if arriving:
        return "ok"
    return "waiting" if elapsed < SIGNAL_GRACE_SECONDS else "warning"


@dataclass(frozen=True)
class DrawerRow:
    label: str
    value: str
    tone: str


def _system_row(health, capture_mode, status, elapsed) -> DrawerRow:
    exited = health.get("helper_exited") and health.get("helper_exit_reason") != "requested_stop"
    if exited or health.get("system_writer_failed"):
        return DrawerRow("System audio", _SYSTEM_FIXES["helper_exited"], "warning")
    if capture_mode != "mic_and_system":
        return DrawerRow("System audio", _SYSTEM_FIXES.get(status, "Unavailable — microphone only"), "warning")
    if health.get("system_writer_lagged"):
        return DrawerRow("System audio", "All apps · falling behind", "warning")
    if health.get("system_first_buffer") and health.get("system_nonzero_signal"):
        return DrawerRow("System audio", "All apps · working", "ok")
    if elapsed < SIGNAL_GRACE_SECONDS:
        return DrawerRow("System audio", "All apps · waiting for audio", "muted")
    return DrawerRow("System audio", "All apps · no sound yet", "warning")


def _mic_row(health) -> DrawerRow:
    if health.get("mic_writer_failed"):
        return DrawerRow("Microphone", "Writer failed — end and restart the meeting", "warning")
    if health.get("mic_writer_lagged"):
        return DrawerRow("Microphone", "You · falling behind", "warning")
    if health.get("mic_first_buffer"):
        return DrawerRow("Microphone", "You · working", "ok")
    return DrawerRow("Microphone", "You · waiting", "muted")


def drawer_rows(health, capture_mode, system_audio_status, elapsed, event_title):
    return [DrawerRow("Event", event_title or "Link to event…", "action"),
            _mic_row(health),
            _system_row(health, capture_mode, system_audio_status, elapsed),
            DrawerRow("Dictation", "Paused until the meeting ends", "muted")]


@dataclass(frozen=True)
class PillView:
    phase: str
    title: str
    detail: str
    indicator: str
    buttons: tuple


class PillMachine:
    """Engine order (engine.py _process_meeting): on_meeting_saved fires
    before the idle state; a cancel fires no saved event; a failure sets
    meeting_processing_error before the idle state."""

    def __init__(self):
        self.phase = "hidden"
        self.saved_id = None

    def on_state(self, state: str, processing_error):
        if state == "meeting_recording":
            self.phase, self.saved_id = "recording", None
        elif state == "meeting_processing":
            if self.phase not in ("confirm_discard", "cancelling", "saved"):
                self.phase = "processing"
        elif self.phase in ("processing", "confirm_discard", "cancelling", "recording"):
            if self.saved_id is not None:
                self.phase = "saved"
            elif processing_error and self.phase != "cancelling":
                self.phase = "failed"
            else:
                self.phase = "hidden"

    def on_saved(self, meeting_id: str):
        self.phase, self.saved_id = "saved", meeting_id

    def cancel(self):
        if self.phase == "processing":
            self.phase = "confirm_discard"

    def keep(self):
        if self.phase == "confirm_discard":
            self.phase = "processing"

    def discard(self):
        if self.phase == "confirm_discard":
            self.phase = "cancelling"

    def expire_saved(self):
        if self.phase == "saved":
            self.phase, self.saved_id = "hidden", None

    def close(self):
        if self.phase in ("failed", "saved"):
            self.phase, self.saved_id = "hidden", None


_BUTTONS = {"recording": ("notes", "stop"), "processing": ("notes", "cancel"),
            "confirm_discard": ("discard", "keep"), "cancelling": (),
            "saved": ("open",), "failed": ("open_meetings", "close"), "hidden": ()}


def pill_view(machine, *, title, elapsed, health, capture_mode, progress) -> PillView:
    phase = machine.phase
    if phase == "recording":
        return PillView(phase, title or UNTITLED, format_timer(elapsed),
                        audio_indicator(health, capture_mode, elapsed), _BUTTONS[phase])
    texts = {"processing": ("Processing", progress or ""),
             "confirm_discard": ("Discard meeting?", ""),
             "cancelling": ("Cancelling…", ""),
             "saved": ("Saved", ""), "failed": ("Couldn't finish", ""), "hidden": ("", "")}
    head, detail = texts[phase]
    return PillView(phase, head, detail, "none", _BUTTONS[phase])


def clamp_origin(origin, size, screens):
    x, y = origin
    w, h = size
    for sx, sy, sw, sh in screens:
        if sx <= x and sy <= y and x + w <= sx + sw and y + h <= sy + sh:
            return (x, y)
    return None


def default_origin(screen, size):
    sx, sy, sw, sh = screen
    w, h = size
    return (sx + (sw - w) / 2, sy + sh - h - 8)
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest -q tests/test_pill_model.py` → PASS.

- [ ] **Step 5: Commit** — `git add speakeasy/ui/pill_model.py tests/test_pill_model.py && git commit -m "Pill model: phases, audio indicator, drawer rows, placement"`

---

### Task 4: Pill and drawer panels (AppKit drawing only)

**Files:**
- Create: `speakeasy/ui/pill_panel.py`
- Test: `tests/test_pill_panel.py`

**Interfaces:**
- Consumes: `PillView`, `DrawerRow` (Task 3); `glass.ClickyButton`, `glass.make_label`; `record_prompt_panel.STOP_RED`, `CORAL`, `ON_ACCENT`.
- Produces: `PillPanel.alloc().initWithTarget_(target)` with python methods `render(view: PillView)`, `show(origin)`, `hide()`, `frame_origin() -> (x, y)`, `set_drawer(rows: list[DrawerRow] | None)` (None hides it), `is_drawer_visible() -> bool`, `event_menu(items: list[tuple[str, str | None, bool]])` (title, key, checked → pops a native menu under the Event row). Target selectors: `pillNotes:`, `pillStop:`, `pillCancel:`, `pillDiscard:`, `pillKeep:`, `pillOpen:`, `pillOpenMeetings:`, `pillClose:`, `pillBodyClicked:`, `pillMovedTo:` (called with a 2-item `NSArray` of floats), `drawerEventClicked:`, `drawerEventChosen:` (an `NSMenuItem` whose `representedObject()` is the key or `None` for Unlink), `drawerClose:`.

- [ ] **Step 1: Write the failing test** — `tests/test_pill_panel.py` (pattern of `tests/test_record_prompt_panel.py`):
```python
from AppKit import NSApplication, NSWindowStyleMaskNonactivatingPanel

from speakeasy.ui.pill_model import DrawerRow, PillView
from speakeasy.ui.pill_panel import PillPanel

NSApplication.sharedApplication()


class Target:
    """Explicit methods: PyObjC dispatches button actions only to methods that
    really exist on the target (a __getattr__ fallback is never consulted)."""

    def __init__(self):
        self.calls = []

    def _hit(self, name):
        self.calls.append(name)

    def pillNotes_(self, s): self._hit("pillNotes_")
    def pillStop_(self, s): self._hit("pillStop_")
    def pillCancel_(self, s): self._hit("pillCancel_")
    def pillDiscard_(self, s): self._hit("pillDiscard_")
    def pillKeep_(self, s): self._hit("pillKeep_")
    def pillOpen_(self, s): self._hit("pillOpen_")
    def pillOpenMeetings_(self, s): self._hit("pillOpenMeetings_")
    def pillClose_(self, s): self._hit("pillClose_")
    def pillBodyClicked_(self, s): self._hit("pillBodyClicked_")
    def pillMovedTo_(self, s): self._hit("pillMovedTo_")
    def drawerEventClicked_(self, s): self._hit("drawerEventClicked_")
    def drawerEventChosen_(self, s): self._hit("drawerEventChosen_")
    def drawerClose_(self, s): self._hit("drawerClose_")


def test_pill_panel_is_non_activating_and_forwards_clicks():
    target = Target()
    p = PillPanel.alloc().initWithTarget_(target)
    assert p._panel.styleMask() & NSWindowStyleMaskNonactivatingPanel
    assert p._panel.becomesKeyOnlyIfNeeded()
    assert not p._panel.hidesOnDeactivate()
    p.render(PillView("recording", "Weekly", "12:03", "ok", ("notes", "stop")))
    p._buttons["notes"].performClick_(None)
    p._buttons["stop"].performClick_(None)
    assert target.calls == ["pillNotes_", "pillStop_"]


def test_render_shows_only_the_phase_buttons():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.render(PillView("recording", "Weekly", "12:03", "ok", ("notes", "stop")))
    assert set(k for k, b in p._buttons.items() if not b.isHidden()) == {"notes", "stop"}
    p.render(PillView("confirm_discard", "Discard meeting?", "", "none", ("discard", "keep")))
    assert set(k for k, b in p._buttons.items() if not b.isHidden()) == {"discard", "keep"}
    assert p._indicator.isHidden()


def test_title_never_overlaps_buttons():
    p = PillPanel.alloc().initWithTarget_(Target())
    for view in (PillView("recording", "x" * 200, "1:02:03", "warning", ("notes", "stop")),
                 PillView("processing", "Processing", "Transcribing meeting… 42%", "none", ("notes", "cancel")),
                 PillView("failed", "Couldn't finish", "", "none", ("open_meetings", "close"))):
        p.render(view)
        right = p._title.frame().origin.x + p._title.frame().size.width
        lefts = [b.frame().origin.x for b in p._buttons.values() if not b.isHidden()]
        assert right <= min(lefts)


def test_show_does_not_make_the_panel_key():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.render(PillView("recording", "Weekly", "00:01", "waiting", ("notes", "stop")))
    p.show((100.0, 700.0))
    assert p._panel.isVisible() and not p._panel.isKeyWindow()
    assert p.frame_origin() == (100.0, 700.0)
    p.hide()
    assert not p._panel.isVisible()


def test_drawer_rows_and_close():
    target = Target()
    p = PillPanel.alloc().initWithTarget_(target)
    p.show((100.0, 700.0))
    p.set_drawer([DrawerRow("Event", "Link to event…", "action"),
                  DrawerRow("Microphone", "You · working", "ok"),
                  DrawerRow("System audio", "All apps · no sound yet", "warning"),
                  DrawerRow("Dictation", "Paused until the meeting ends", "muted")])
    assert p.is_drawer_visible()
    p._drawer_close.performClick_(None)
    assert target.calls[-1] == "drawerClose_"
    p.set_drawer(None)
    assert not p.is_drawer_visible()
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest -q tests/test_pill_panel.py` → FAIL (module missing).

- [ ] **Step 3: Implement** `speakeasy/ui/pill_panel.py`. Requirements (write the code to these; reuse the banner's construction code for panel flags, `ClickyButton`, `make_label`, `_dynamic` colours):
  - `_make_panel(width, height, radius, material)` helper builds a borderless non-activating `NSPanel` with the Global Constraints flags and an `NSVisualEffectView` content view (blending behind-window, state active, layer corner radius, masks to bounds).
  - Pill content view is a `PillBackground(NSView)` subclass placed under the effect view's subviews that implements `mouseDown_`, `mouseDragged_`, `mouseUp_`: record `NSEvent.mouseLocation()` and the window origin on down; on drag move the window by the delta (`setFrameOrigin_`); on up, if the pointer moved more than 3 pt call `target.pillMovedTo_(NSArray.arrayWithArray_([x, y]))`, else call `target.pillBodyClicked_(None)`. It must return `True` from `acceptsFirstMouse_` so the first click works on an inactive app.
  - Layout (points, origin bottom-left, pill 360 × 36): recording dot 8 × 8 at x 14, y 14 (layer background `STOP_RED`, corner radius 4; hidden outside recording); `_title` label 13 pt semibold, truncating tail, starts at x 28; `_detail` label 12.5 pt monospaced-digit (`NSFont.monospacedDigitSystemFontOfSize_weight_`), secondary colour; `_indicator` 8 × 8 dot; buttons right-aligned from x = 354 leftwards with 6 pt gaps, height 24, y 6: `stop` 26 × 26 circle (y 5, `STOP_RED` bezel, title "■" in `ON_ACCENT`, tooltip "End Meeting"), `notes` 60 "Notes", `cancel` 64 "Cancel", `discard` 70 "Discard" (`STOP_RED`), `keep` 56 "Keep", `open` 56 "Open", `open_meetings` 110 "Open Meetings", `close` 24 "×" borderless (tooltip "Close").
  - `render(view)`: hide all buttons, show `view.buttons` in that right-to-left order (last id is rightmost), then lay out text: in `recording` the detail (timer) is right-aligned 52 pt wide immediately left of the indicator (indicator 10 pt left of the leftmost button); title fills from x 28 to 6 pt left of the timer. In other phases the indicator and dot are hidden and title + detail share the space from x 16 to 8 pt left of the leftmost button (title sized to fit, detail takes the rest, both truncating). Indicator colours: ok → `systemGreenColor`, warning → `systemOrangeColor`, waiting → `tertiaryLabelColor`. Saved phase prefixes the title with "✓ ".
  - `show(origin)`: set frame origin, fade in like the banner's `_fade_in` when not visible, `orderFrontRegardless()`; never `makeKeyWindow`/`makeKeyAndOrderFront_`. `hide()` orders out pill and drawer.
  - Drawer: 360 wide (= PILL_W); rows 24 pt each from the top with 10 pt padding; label column 96 pt secondary 12 pt, value 12 pt (`warning` → orange, `ok` → labelColor, `muted` → secondary); the Event row's value is a borderless `ClickyButton` (`drawerEventClicked:`) left-aligned in the value column; ✕ close button (`self._drawer_close`, `drawerClose:`) top-right 18 × 18; footer label "Audio stays on this Mac and is deleted once the transcript is saved." 11 pt tertiary, wrapping to two lines (height 30). Height = 10 + 24 × len(rows) + 6 + 30 + 10. Position: same x as the pill, 6 pt gap; re-positioned whenever the pill moves or is shown.
  - `event_menu(items)`: build an `NSMenu` (one item per `(title, key, checked)`; state on for checked; `representedObject` = key; action `drawerEventChosen:` target = target), then `popUpMenuPositioningItem_atLocation_inView_(None, (0, 0), event_button)`.
  - Keep the module free of engine imports.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest -q tests/test_pill_panel.py tests/test_record_prompt_panel.py` → PASS.

- [ ] **Step 5: Commit** — `git add speakeasy/ui/pill_panel.py tests/test_pill_panel.py && git commit -m "Pill and capture-drawer panels"`

---

### Task 5: Pill controller and app wiring

**Files:**
- Create: `speakeasy/ui/pill_controller.py`
- Modify: `speakeasy/ui/menubar.py` (`AppDelegate.applicationDidFinishLaunching_` callbacks, `applicationWillTerminate_`)
- Test: `tests/test_pill_controller.py`

**Interfaces:**
- Consumes: `PillMachine`, `pill_view`, `drawer_rows`, `clamp_origin`, `default_origin` (Task 3); `PillPanel` (Task 4) via module-level `_make_panel(target)`; `settings.get_pill_origin`/`set_pill_origin` (Task 1); `StatusItemController.open_meetings(view)` (Task 2) passed in as `opener`; `calendar_payloads.day_chips`; `services.calendar_sync`.
- Produces: `PillController.alloc().initWithEngine_opener_(engine, opener)`; selectors `engineStateChanged:`, `meetingProgress:`, `meetingSaved:`, `tick:`, `savedExpired:` and every panel target selector in Task 4; python methods `shutdown()`; module functions `_make_panel(target)`, `_screens() -> list[tuple]` (visible frames), `_mouse_screen() -> tuple`, `_today_events() -> list[dict] | None` (None when Calendar isn't connected) — all monkeypatchable in tests.

- [ ] **Step 1: Write the failing tests** — `tests/test_pill_controller.py`:
```python
from types import SimpleNamespace

import pytest

from speakeasy import settings
from speakeasy.ui import pill_controller as pc


class FakePanel:
    def __init__(self):
        self.views, self.shown, self.drawer, self.menus = [], [], None, []
        self.visible = False
        self.origin = None

    def render(self, view): self.views.append(view)
    def show(self, origin): self.visible, self.origin = True, origin; self.shown.append(origin)
    def hide(self): self.visible = False; self.drawer = None
    def frame_origin(self): return self.origin
    def set_drawer(self, rows): self.drawer = rows
    def is_drawer_visible(self): return self.drawer is not None
    def event_menu(self, items): self.menus.append(items)


class Health:
    def __init__(self, **kw): self.kw = kw
    def to_dict(self): return dict(self.kw)


def make_engine(state="ready"):
    calls = []
    engine = SimpleNamespace(
        state=SimpleNamespace(value=state), meeting_event=None, meeting_processing_error=None,
        meeting_recorder=SimpleNamespace(elapsed_seconds=5.0, capture_mode="mic_and_system",
                                         system_audio_status="available",
                                         health=Health(mic_first_buffer=True, system_first_buffer=True,
                                                       system_nonzero_signal=True)),
        end_meeting=lambda: calls.append("end"),
        cancel_meeting_processing=lambda: calls.append("cancel"),
        link_meeting_event=lambda key: calls.append(("link", key)))
    return engine, calls


@pytest.fixture
def setup(monkeypatch):
    panel = FakePanel()
    monkeypatch.setattr(pc, "_make_panel", lambda target: panel)
    monkeypatch.setattr(pc, "_screens", lambda: [(0, 0, 1440, 875)])
    monkeypatch.setattr(pc, "_mouse_screen", lambda: (0, 0, 1440, 875))
    monkeypatch.setattr(pc, "_today_events", lambda: [{"key": "e1", "title": "Weekly", "time": "9:00 AM"}])
    monkeypatch.setattr(pc, "_start_timer", lambda target, seconds, selector, repeats: SimpleNamespace(invalidate=lambda: None))
    engine, calls = make_engine()
    opened = []
    pill = pc.PillController.alloc().initWithEngine_opener_(engine, opened.append)
    return SimpleNamespace(pill=pill, panel=panel, engine=engine, calls=calls, opened=opened)


def go(s, state, error=None):
    s.engine.state.value = state
    s.engine.meeting_processing_error = error
    s.pill.engineStateChanged_(state)


def test_pill_shows_on_any_meeting_recording_state(setup):
    go(setup, "meeting_recording")
    assert setup.panel.visible and setup.panel.origin == (540.0, 831.0)
    assert setup.panel.views[-1].buttons == ("notes", "stop")


def test_hidden_while_idle(setup):
    go(setup, "ready")
    assert not setup.panel.visible


def test_notes_opens_recording_and_stop_ends(setup):
    go(setup, "meeting_recording")
    setup.pill.pillNotes_(None)
    setup.pill.pillStop_(None)
    assert setup.opened == ["recording"] and setup.calls == ["end"]


def test_cancel_discard_cancels_engine_and_hides_on_idle(setup):
    go(setup, "meeting_processing")
    setup.pill.pillCancel_(None)
    assert setup.panel.views[-1].title == "Discard meeting?"
    setup.pill.pillDiscard_(None)
    assert setup.calls == ["cancel"]
    go(setup, "ready")
    assert not setup.panel.visible


def test_saved_then_open_opens_that_meeting(setup):
    go(setup, "meeting_processing")
    setup.pill.meetingSaved_("m-1")
    go(setup, "ready")
    assert setup.panel.views[-1].title == "Saved"
    setup.pill.pillOpen_(None)
    assert setup.opened == [{"meeting": "m-1"}]
    assert not setup.panel.visible


def test_saved_expires(setup):
    go(setup, "meeting_processing")
    setup.pill.meetingSaved_("m-1")
    setup.pill.savedExpired_(None)
    assert not setup.panel.visible


def test_failed_open_meetings_and_close(setup):
    go(setup, "meeting_processing")
    go(setup, "ready", "processing_failed")
    assert setup.panel.views[-1].title == "Couldn't finish"
    setup.pill.pillOpenMeetings_(None)
    assert setup.opened == [None]
    setup.pill.pillClose_(None)
    assert not setup.panel.visible


def test_progress_text_reaches_the_pill(setup):
    go(setup, "meeting_processing")
    setup.pill.meetingProgress_("Transcribing meeting… 42%")
    assert setup.panel.views[-1].detail == "Transcribing meeting… 42%"


def test_moved_position_is_saved_and_reused(setup):
    setup.pill.pillMovedTo_([100.0, 500.0])
    assert settings.get_pill_origin() == (100.0, 500.0)
    go(setup, "meeting_recording")
    assert setup.panel.origin == (100.0, 500.0)


def test_saved_origin_offscreen_uses_default(setup):
    settings.set_pill_origin(5000.0, 500.0)
    go(setup, "meeting_recording")
    assert setup.panel.origin == (540.0, 831.0)


def test_body_click_toggles_drawer(setup):
    go(setup, "meeting_recording")
    setup.pill.pillBodyClicked_(None)
    assert [r.label for r in setup.panel.drawer] == ["Event", "Microphone", "System audio", "Dictation"]
    setup.pill.pillBodyClicked_(None)
    assert setup.panel.drawer is None


def test_warning_opens_drawer_once(setup):
    go(setup, "meeting_recording")
    setup.engine.meeting_recorder.health = Health(helper_exited=True, helper_exit_reason="crash")
    setup.pill.tick_(None)
    assert setup.panel.drawer is not None
    setup.pill.drawerClose_(None)
    setup.pill.tick_(None)
    assert setup.panel.drawer is None


def test_event_menu_links_and_unlinks(setup):
    go(setup, "meeting_recording")
    setup.pill.pillBodyClicked_(None)
    setup.pill.drawerEventClicked_(None)
    assert setup.panel.menus[-1] == [("9:00 AM · Weekly", "e1", False)]
    setup.pill.drawerEventChosen_(SimpleNamespace(representedObject=lambda: "e1"))
    setup.engine.meeting_event = SimpleNamespace(event_key="e1", title="Weekly")
    setup.pill.drawerEventClicked_(None)
    assert setup.panel.menus[-1] == [("9:00 AM · Weekly", "e1", True), ("Unlink", None, False)]
    setup.pill.drawerEventChosen_(SimpleNamespace(representedObject=lambda: None))
    assert setup.calls == [("link", "e1"), ("link", None)]


def test_no_calendar_means_no_event_menu(setup, monkeypatch):
    monkeypatch.setattr(pc, "_today_events", lambda: None)
    go(setup, "meeting_recording")
    setup.pill.drawerEventClicked_(None)
    assert setup.panel.menus == []
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest -q tests/test_pill_controller.py` → FAIL.

- [ ] **Step 3: Implement** `speakeasy/ui/pill_controller.py`:
```python
"""Main-thread glue for the recording pill: follows engine state (never the
start path), draws pill_model's view on PillPanel and turns clicks into
engine calls or Meetings navigation."""

import objc
from Foundation import NSObject, NSTimer

from .. import settings
from .pill_model import (PillMachine, audio_indicator, clamp_origin, default_origin,
                         drawer_rows, pill_view)

SIZE = (360.0, 36.0)
SAVED_SECONDS = 6.0


def _make_panel(target):
    from .pill_panel import PillPanel
    return PillPanel.alloc().initWithTarget_(target)


def _screens():
    from AppKit import NSScreen
    return [tuple((s.visibleFrame().origin.x, s.visibleFrame().origin.y,
                   s.visibleFrame().size.width, s.visibleFrame().size.height))
            for s in NSScreen.screens()]


def _mouse_screen():
    from AppKit import NSEvent, NSMouseInRect, NSScreen
    point = NSEvent.mouseLocation()
    for s in NSScreen.screens():
        if NSMouseInRect(point, s.frame(), False):
            f = s.visibleFrame()
            return (f.origin.x, f.origin.y, f.size.width, f.size.height)
    return _screens()[0]


def _today_events():
    from datetime import datetime
    from ..meeting_library import MeetingLibrary
    from . import calendar_payloads, services
    sync = services.calendar_sync
    if sync is None or sync.access() != "connected":
        return None
    now = datetime.now().astimezone()
    day = now.date().isoformat()
    return calendar_payloads.day_chips(MeetingLibrary().calendar_events_between(day, day), now.tzinfo)


def _start_timer(target, seconds, selector, repeats):
    return NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        seconds, target, selector, None, repeats)


class PillController(NSObject):
    def initWithEngine_opener_(self, engine, opener):
        self = objc.super(PillController, self).init()
        if self is None:
            return None
        self.engine, self.opener = engine, opener
        self.machine = PillMachine()
        self.panel = _make_panel(self)
        self._progress = None
        self._ticker = None
        self._saved_timer = None
        self._drawer_open = False
        self._auto_opened = False
        return self
    ...
```
Remaining behaviour (implement exactly; every method is main-thread):
- `engineStateChanged_(value)`: `self.machine.on_state(str(value), self.engine.meeting_processing_error)`; on entering `"recording"` reset `_auto_opened`, `_drawer_open`, `_progress`; then `_render()`.
- `meetingProgress_(text)`: store `str(text)`, `_render()`. `meetingSaved_(id)`: `machine.on_saved(str(id))`, `_render()`.
- `_render()`: if `machine.phase == "hidden"`: stop timers, `panel.hide()`, return. Build the view with `pill_view(machine, title=event.title if event else None, elapsed=recorder.elapsed_seconds, health=recorder.health.to_dict() if recorder.health else {}, capture_mode=recorder.capture_mode, progress=self._progress)` (read `meeting_recorder` and `meeting_event` once into locals; treat missing attributes as `None`/`{}`/`""`). `panel.render(view)`; if the panel isn't visible, `panel.show(self._origin())`. Start the 1 s repeating `tick:` timer while phase is `recording` or `processing` (one timer only); stop it otherwise. When phase becomes `saved`, start a one-shot 6 s `savedExpired:` timer (replace any previous). Create every timer through the module-level `_start_timer(self, seconds, selector, repeats)` (tests replace it) and stop one with `.invalidate()`. Drawer: shown only in `recording`; when `_drawer_open`, `panel.set_drawer(drawer_rows(...))`, else `panel.set_drawer(None)`.
- `tick_`: if phase is `recording` and `audio_indicator(...) == "warning"` and not `_auto_opened`: set `_auto_opened = _drawer_open = True`. Then `_render()`.
- `_origin()`: saved = `settings.get_pill_origin()`; if saved and `clamp_origin(saved, SIZE, _screens())` return it; else `default_origin(_mouse_screen(), SIZE)`.
- Buttons: `pillNotes_` → `opener("recording")`; `pillStop_` → `engine.end_meeting()`; `pillCancel_` → `machine.cancel()`; `pillKeep_` → `machine.keep()`; `pillDiscard_` → `machine.discard()` then `engine.cancel_meeting_processing()`; `pillOpen_` → `opener({"meeting": machine.saved_id})` then `machine.close()`; `pillOpenMeetings_` → `opener(None)`; `pillClose_` → `machine.close()`; each ends with `_render()`.
- `pillBodyClicked_` → toggle `_drawer_open` (only in `recording`), `_render()`. `drawerClose_` → `_drawer_open = False`, `_render()`.
- `pillMovedTo_(pair)` → `settings.set_pill_origin(float(pair[0]), float(pair[1]))` (ignore ValueError).
- `drawerEventClicked_`: `chips = _today_events()`; if None or empty, return. Linked key = `engine.meeting_event.event_key` if any. Items = `[(f"{c['time']} · {c['title']}", c["key"], c["key"] == linked) for c in chips]` plus `("Unlink", None, False)` when linked. `panel.event_menu(items)`.
- `drawerEventChosen_(item)` → `engine.link_meeting_event(item.representedObject())`; `_render()`.
- `shutdown()` stops both timers and hides the panel.

`menubar.py` `AppDelegate.applicationDidFinishLaunching_`: after `self.controller` exists:
```python
        from .pill_controller import PillController

        self.pill = PillController.alloc().initWithEngine_opener_(engine, self.controller.open_meetings)
```
and in `on_state_changed`, `on_meeting_progress`, `on_meeting_saved` add the same `performSelectorOnMainThread_withObject_waitUntilDone_` call on `self.pill` (`engineStateChanged:`, `meetingProgress:`, `meetingSaved:`); after `engine.start()` call `self.pill.engineStateChanged_(engine.state.value)`. `applicationWillTerminate_` calls `self.pill.shutdown()` when present. (Dock-window wiring is removed in Task 8, not here.)

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest -q tests/test_pill_controller.py tests/test_pill_model.py tests/test_pill_panel.py` → PASS.

- [ ] **Step 5: Commit** — `git add speakeasy/ui/pill_controller.py speakeasy/ui/menubar.py tests/test_pill_controller.py && git commit -m "Recording pill controller, wired to engine callbacks"`

---

### Task 6: Menu-bar menu with the Next-up card

**Files:**
- Create: `speakeasy/ui/next_up.py`, `speakeasy/ui/next_up_view.py`, `speakeasy/ui/menu_model.py`
- Modify: `speakeasy/ui/menubar.py` (`StatusItemController.initWithEngine_`, `engineStateChanged_`, `_sync_meeting_items`, new `menuWillOpen_`, `openDiagnostic_`, `openSettings_`, `openMeetingNotes_`, `retryMicrophone_`, `nextUpRecord_`; remove the Start at Login item, `_sync_login_item`, `toggleLogin_`)
- Test: `tests/test_next_up.py`, `tests/test_menu_model.py`, `tests/test_next_up_view.py`

**Interfaces:**
- Consumes: `default_options` (Task 1), `open_meetings` (Task 2), `calendar_payloads.attendee_count`, `_visible`, `calendar_match.event_start/event_end`.
- Produces: `next_up(events, now: datetime, recording_key: str | None) -> dict | None` returning `{"key", "title", "subtitle"}`; `menu_flags(state: str) -> dict` with keys `next_up`, `meeting_notes`, `retry_mic`, `cancel`; `NextUpView.alloc().initWithTarget_(target)` with `set_event(title, subtitle)` and a Record button calling `target.nextUpRecord_`.

- [ ] **Step 1: Write the failing tests**

`tests/test_next_up.py` (reuse the `ev` helper style from `tests/test_record_prompt_controller.py`):
```python
from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import CalendarEvent, EventPerson
from speakeasy.ui.next_up import next_up

UTC = timezone.utc
NOW = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ev(key, start, minutes=30, people=2, recorded=False, title=None, all_day=False):
    e = CalendarEvent(key, "Work", title or key, start.strftime(FMT),
                      (start + timedelta(minutes=minutes)).strftime(FMT), all_day, False, [],
                      other_attendees=people - 1 if people else None,
                      people=[EventPerson("A", None, "attendee")] * people)
    if recorded:
        e.meeting_ids.append("m-1")
    return e


def test_in_progress_event_is_now():
    got = next_up([ev("a", NOW - timedelta(minutes=5))], NOW, None)
    assert got == {"key": "a", "title": "a", "subtitle": "Now · 2 people"}


def test_next_event_within_an_hour_counts_minutes():
    got = next_up([ev("b", NOW + timedelta(minutes=4), people=1)], NOW, None)
    assert got["subtitle"] == "Starts in 4 min · 1 person"


def test_later_event_shows_clock_time():
    got = next_up([ev("c", NOW + timedelta(hours=2))], NOW, None)
    assert got["subtitle"].startswith("at ")


def test_skips_recorded_finished_recording_and_all_day():
    events = [ev("done", NOW - timedelta(hours=2)), ev("rec", NOW - timedelta(minutes=1), recorded=True),
              ev("live", NOW - timedelta(minutes=1)), ev("allday", NOW, all_day=True),
              ev("next", NOW + timedelta(minutes=30))]
    assert next_up(events, NOW, "live")["key"] == "next"


def test_nothing_left_is_none():
    assert next_up([ev("done", NOW - timedelta(hours=2))], NOW, None) is None
```
(Check `CalendarEvent`'s real field order and whether `meeting_ids` is a list attribute or constructor argument in `speakeasy/meeting_library.py`, and adapt the helper — `tests/test_calendar_payloads.py` already builds events; copy its helper.)

`tests/test_menu_model.py`:
```python
import pytest

from speakeasy.ui.menu_model import menu_flags


@pytest.mark.parametrize("state,flags", [
    ("ready", dict(next_up=True, meeting_notes=False, retry_mic=False, cancel=False)),
    ("meeting_recording", dict(next_up=False, meeting_notes=True, retry_mic=False, cancel=False)),
    ("meeting_processing", dict(next_up=False, meeting_notes=True, retry_mic=False, cancel=True)),
    ("mic_failed", dict(next_up=False, meeting_notes=False, retry_mic=True, cancel=False)),
    ("loading", dict(next_up=False, meeting_notes=False, retry_mic=False, cancel=False)),
])
def test_menu_flags(state, flags):
    assert menu_flags(state) == flags
```

`tests/test_next_up_view.py`:
```python
from AppKit import NSApplication

from speakeasy.ui.next_up_view import NextUpView

NSApplication.sharedApplication()


class Target:
    def __init__(self): self.clicks = 0
    def nextUpRecord_(self, sender): self.clicks += 1


def test_view_shows_event_and_forwards_record():
    t = Target()
    v = NextUpView.alloc().initWithTarget_(t)
    v.set_event("Weekly 1:1 — Alex", "Starts in 4 min · 2 people")
    assert v._title.stringValue() == "Weekly 1:1 — Alex"
    assert v._subtitle.stringValue() == "Starts in 4 min · 2 people"
    v._record.performClick_(None)
    assert t.clicks == 1
    assert v.view().frame().size.width == 280
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest -q tests/test_next_up.py tests/test_menu_model.py tests/test_next_up_view.py` → FAIL.

- [ ] **Step 3: Implement**

`speakeasy/ui/next_up.py`:
```python
"""The menu's Next-up card: the event in progress, else the next one today."""

from ..calendar_match import event_end, event_start
from .calendar_payloads import _visible, attendee_count


def _people(n: int) -> str:
    return "1 person" if n == 1 else f"{n} people"


def next_up(events, now, recording_key):
    candidates = []
    for e in filter(_visible, events):
        start, end = event_start(e).astimezone(now.tzinfo), event_end(e).astimezone(now.tzinfo)
        if end <= now or e.meeting_ids or e.event_key == recording_key or start.date() != now.date():
            continue
        candidates.append((start, end, e))
    if not candidates:
        return None
    start, end, e = min(candidates, key=lambda c: (c[0], c[2].event_key))
    if start <= now:
        when = "Now"
    elif (start - now).total_seconds() < 3600:
        when = f"Starts in {max(1, round((start - now).total_seconds() / 60))} min"
    else:
        when = "at " + start.strftime("%-I:%M %p")
    return {"key": e.event_key, "title": e.title, "subtitle": f"{when} · {_people(attendee_count(e))}"}
```
`speakeasy/ui/menu_model.py`:
```python
def menu_flags(state: str) -> dict:
    return {"next_up": state == "ready",
            "meeting_notes": state in ("meeting_recording", "meeting_processing"),
            "retry_mic": state == "mic_failed",
            "cancel": state == "meeting_processing"}
```
`speakeasy/ui/next_up_view.py`: an `NSObject` holding an `NSView` 280 × 54: `_title` (13 pt semibold, truncating tail, x 14, y 28, width 280 − 14 − 84), `_subtitle` (11.5 pt secondary, x 14, y 10), `_record` `ClickyButton` 72 × 24 at x 280 − 14 − 72, y 15, bezel `CORAL`, attributed title "Record" in `ON_ACCENT` 12.5 pt semibold, action `nextUpRecord:` on the target. `view()` returns the `NSView`; `set_event(title, subtitle)` sets both labels.

`menubar.py` `StatusItemController`: rebuild the menu in this order (reuse existing items; titles verbatim from Global Constraints):
1. `_status_line`, `_event_line`, separator
2. `_next_up_item` (`NSMenuItem` with `setView_(self._next_up.view())`, hidden by default)
3. `_meeting_item` ("Start Meeting"/"End Meeting (mm:ss)" via `toggleMeeting:`), `_cancel_item`, `_retry_item` ("Retry Microphone", `retryMicrophone:` → `engine.retry_microphone()`), separator
4. `_meetings_item` ("Open Meetings", key equivalent "o"), `_notes_item` ("Meeting Notes", `openMeetingNotes:` → `open_meetings("recording")`), separator
5. Profile ▸, `_train_item` ("Train My Voice…"), `_diagnostic_item` ("Check Microphone…", `openDiagnostic:`), Correct/Copy/Paste Last Dictation, separator
6. "Settings…" (`openSettings:` → `open_meetings("settings")`, key equivalent ","), "Quit Speakeasy" (⌘Q)
Set `menu.setDelegate_(self)`. `menuWillOpen_(menu)`: `flags = menu_flags(self.engine.state.value)`; set hidden states for `_notes_item`, `_retry_item`, `_cancel_item`; for the card, when `flags["next_up"]`, compute `next_up(events, now, None)` with events from `MeetingLibrary().calendar_events_between(today, today)` only if `services.calendar_sync` is connected (wrap the library read in `try/except Exception` → hide the card); show and fill it when not None, else hide. `nextUpRecord_`: `self._item.menu().cancelTracking()`, then `self.engine.begin_meeting(default_options(calendar_event_key=self._next_up_key))`. `openDiagnostic_`: lazily build `DiagnosticWindowController.alloc().initWithEngine_(self.engine)` (moved from `main_window._open_window`) and `show()`. `_sync_meeting_items` uses "Start Meeting" instead of "Begin Meeting". Delete `_login_item`, `_sync_login_item`, `toggleLogin_` (Settings › General replaces them). `engineStateChanged_` also applies `menu_flags` so items are right even if the menu is already open.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest -q tests/test_next_up.py tests/test_menu_model.py tests/test_next_up_view.py` and a smoke import: `.venv/bin/python -c "import speakeasy.ui.menubar"` → PASS / no error.

- [ ] **Step 5: Commit** — `git add speakeasy/ui tests && git commit -m "Menu bar: Next-up card, Meeting Notes, Retry Microphone, Check Microphone, Settings"`

---

### Task 7: Today focus layout and engine banners

**Files:**
- Modify: `speakeasy/ui/calendar_payloads.py` (`agenda_row` adds `start`, `end`)
- Modify: `speakeasy/ui/meetings_window.py` (`recording_info` adds `mode`, `micFailure`, `startError`, `processingError`)
- Modify: `speakeasy/ui/meetings_bridge.py` (`_NOT_RECORDING` defaults, `meeting.retryMicrophone`, constructor kwarg `retry_microphone=None`)
- Create: `frontend/src/meetings/agendaSplit.ts`, `frontend/tests/agendaSplit.test.ts`
- Modify: `frontend/src/mock/meetings.ts` (`AgendaEvent.start/end`, `RecordingInfo` optional fields, mock agenda with 9 events), `frontend/src/meetings/TodayView.tsx`, `frontend/src/meetings/TodayView.module.css`, `frontend/src/meetings/App.tsx`
- Test: `tests/test_calendar_payloads.py`, `tests/test_meetings_bridge.py`

**Interfaces:**
- Consumes: `meeting.start` (Task 1), `onSelectRecording` (existing), `recording.get` (existing).
- Produces: agenda rows `start: str` / `end: str` (local ISO with offset); `RecordingInfo` optional `mode?: string; micFailure?: string | null; startError?: string | null; processingError?: string | null`; `splitAgenda(agenda: AgendaEvent[], nowMs: number, recordingActive: boolean) -> { hero: AgendaEvent | null; heroKind: 'now' | 'next' | null; next: AgendaEvent[]; more: AgendaEvent[]; earlier: AgendaEvent[]; earlierRecorded: number }`; `heroWhen(event, nowMs) -> string`; `engineBanner(rec: RecordingInfo) -> { text: string; action: 'retry' | null } | null`.

- [ ] **Step 1: Write the failing tests**

`tests/test_calendar_payloads.py` — add:
```python
def test_agenda_row_carries_local_iso_times():
    # build one event with the file's existing helper, then:
    row = agenda_row(event, now, None)
    assert row["start"] == event_start(event).astimezone(now.tzinfo).isoformat()
    assert row["end"] == event_end(event).astimezone(now.tzinfo).isoformat()
```
`tests/test_meetings_bridge.py` — add:
```python
def test_retry_microphone_calls_engine(tmp_path):
    calls = []
    bridge = make_bridge(tmp_path, retry_microphone=lambda: calls.append(1) or True)
    assert bridge.retry_microphone_payload({}) is True and calls == [1]


def test_recording_get_defaults_include_engine_fields(tmp_path):
    got = make_bridge(tmp_path).recording_payload({})
    assert got["mode"] == "" and got["micFailure"] is None
    assert got["startError"] is None and got["processingError"] is None
```
`frontend/tests/agendaSplit.test.ts`:
```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgendaEvent } from '../src/mock/meetings.ts';
import { engineBanner, heroWhen, splitAgenda } from '../src/meetings/agendaSplit.ts';

const base = Date.parse('2026-10-03T12:00:00+01:00');
function ev(key: string, startMin: number, durMin = 30, status: AgendaEvent['status'] = 'none'): AgendaEvent {
  const s = new Date(base + startMin * 60_000), e = new Date(base + (startMin + durMin) * 60_000);
  return { key, time: '', endTime: '', title: key, attendeeCount: 2, status, meetingId: status === 'recorded' ? 'm' : null,
           start: s.toISOString(), end: e.toISOString() };
}

test('in-progress event is the hero; the next three follow; the rest are folded', () => {
  const day = [ev('a', -240), ev('b', -120, 30, 'recorded'), ev('now', -10), ...[1, 2, 3, 4, 5].map((i) => ev(`n${i}`, i * 60))];
  const s = splitAgenda(day, base, false);
  assert.equal(s.hero?.key, 'now'); assert.equal(s.heroKind, 'now');
  assert.deepEqual(s.next.map((e) => e.key), ['n1', 'n2', 'n3']);
  assert.deepEqual(s.more.map((e) => e.key), ['n4', 'n5']);
  assert.deepEqual(s.earlier.map((e) => e.key), ['a', 'b']);
  assert.equal(s.earlierRecorded, 1);
});

test('without an event in progress the next one is the hero', () => {
  const s = splitAgenda([ev('a', -120), ev('x', 30), ev('y', 90)], base, false);
  assert.equal(s.hero?.key, 'x'); assert.equal(s.heroKind, 'next');
  assert.deepEqual(s.next.map((e) => e.key), ['y']);
});

test('a recorded event in progress is not the hero', () => {
  const s = splitAgenda([ev('r', -10, 30, 'recorded'), ev('x', 30)], base, false);
  assert.equal(s.hero?.key, 'x');
  assert.deepEqual(s.next.map((e) => e.key), ['r']);
});

test('while recording, the hero slot belongs to the recording', () => {
  const s = splitAgenda([ev('live', -5, 30, 'recording'), ev('x', 30)], base, true);
  assert.equal(s.hero, null); assert.equal(s.heroKind, null);
  assert.deepEqual(s.next.map((e) => e.key), ['x']);
});

test('end of day: nothing left', () => {
  const s = splitAgenda([ev('a', -120)], base, false);
  assert.equal(s.hero, null); assert.equal(s.next.length, 0); assert.equal(s.earlier.length, 1);
});

test('a busy day of 20 meetings shows at most 4 rows before folding', () => {
  const day = Array.from({ length: 20 }, (_, i) => ev(`e${i}`, (i - 10) * 30, 25));
  const s = splitAgenda(day, base, false);
  assert.ok((s.hero ? 1 : 0) + s.next.length <= 4);
  assert.equal(s.earlier.length + (s.hero ? 1 : 0) + s.next.length + s.more.length, 20);
});

test('heroWhen', () => {
  assert.equal(heroWhen(ev('a', -5), base), 'Now');
  assert.equal(heroWhen(ev('a', 4), base), 'Starts in 4 min');
  assert.match(heroWhen({ ...ev('a', 120), time: '2:00 PM' }, base), /^at 2:00 PM$/);
});

test('engine banners', () => {
  const idle = { recording: false, processing: false, startedAt: null, title: null };
  assert.equal(engineBanner(idle), null);
  assert.deepEqual(engineBanner({ ...idle, mode: 'mic_failed', micFailure: 'device_unavailable' }),
    { text: 'Connect a microphone or select an input, then retry', action: 'retry' });
  assert.equal(engineBanner({ ...idle, mode: 'ready', startError: 'microphone_busy' })?.text,
    'Microphone is still being released. Wait, then try again.');
  assert.equal(engineBanner({ ...idle, mode: 'ready', processingError: 'processing_failed' })?.text,
    'Meeting could not finish normally. Check saved meetings before trying again.');
});
```

- [ ] **Step 2: Run to verify failure** — pytest on the two files + `npm --prefix frontend test` → FAIL.

- [ ] **Step 3: Implement**

`calendar_payloads.agenda_row`: add `"start": start.isoformat(), "end": end.isoformat()` to the returned dict.

`meetings_window.recording_info`: add
```python
                    "mode": state,
                    "micFailure": getattr(engine.recorder, "last_failure", None),
                    "startError": engine.meeting_start_error,
                    "processingError": engine.meeting_processing_error,
```
`meetings_bridge._NOT_RECORDING` gains `"mode": "", "micFailure": None, "startError": None, "processingError": None`; register `"meeting.retryMicrophone": self.retry_microphone_payload`:
```python
    def retry_microphone_payload(self, params) -> bool:
        if self._retry_microphone is None:
            raise BridgeError("recording_unavailable")
        return bool(self._retry_microphone())
```
and pass `retry_microphone=engine.retry_microphone if engine is not None else None` from `meetings_window.py`.

`frontend/src/meetings/agendaSplit.ts` (pure; only type imports):
```ts
import type { AgendaEvent, RecordingInfo } from '../mock/meetings';

export interface AgendaSplit {
  hero: AgendaEvent | null; heroKind: 'now' | 'next' | null;
  next: AgendaEvent[]; more: AgendaEvent[]; earlier: AgendaEvent[]; earlierRecorded: number;
}
const VISIBLE_NEXT = 3;

function endMs(e: AgendaEvent): number {
  return e.end ? Date.parse(e.end) : Date.parse(e.start);
}

export function splitAgenda(agenda: AgendaEvent[], nowMs: number, recordingActive: boolean): AgendaSplit {
  const sorted = [...agenda].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const earlier = sorted.filter((e) => endMs(e) <= nowMs && e.status !== 'recording');
  const remaining = sorted.filter((e) => endMs(e) > nowMs && e.status !== 'recording');
  let hero: AgendaEvent | null = null;
  let heroKind: AgendaSplit['heroKind'] = null;
  if (!recordingActive) {
    hero = remaining.find((e) => Date.parse(e.start) <= nowMs && e.status !== 'recorded') ?? null;
    heroKind = hero ? 'now' : null;
    if (!hero) {
      hero = remaining.find((e) => Date.parse(e.start) > nowMs) ?? null;
      heroKind = hero ? 'next' : null;
    }
  }
  const later = remaining.filter((e) => e !== hero);
  return { hero, heroKind, next: later.slice(0, VISIBLE_NEXT), more: later.slice(VISIBLE_NEXT),
           earlier, earlierRecorded: earlier.filter((e) => e.status === 'recorded').length };
}

export function heroWhen(e: AgendaEvent, nowMs: number): string {
  const start = Date.parse(e.start);
  if (start <= nowMs) return 'Now';
  const minutes = Math.max(1, Math.round((start - nowMs) / 60_000));
  return minutes < 60 ? `Starts in ${minutes} min` : `at ${e.time}`;
}

export function microphoneFailureText(reason?: string | null): string {
  if (reason === 'permission_blocked') return 'Allow Microphone in System Settings, then retry';
  if (reason === 'device_unavailable') return 'Connect a microphone or select an input, then retry';
  if (reason === 'teardown_pending') return 'Audio shutdown is still pending — restart Speakeasy';
  if (reason === 'helper_timeout') return 'Microphone did not respond — select Retry Microphone';
  return 'Microphone unavailable — select Retry Microphone';
}

export function engineBanner(rec: RecordingInfo): { text: string; action: 'retry' | null } | null {
  if (rec.mode === 'mic_failed') return { text: microphoneFailureText(rec.micFailure), action: 'retry' };
  if (rec.mode === 'ready' && rec.startError) {
    return { text: rec.startError === 'microphone_busy'
      ? 'Microphone is still being released. Wait, then try again.'
      : 'Meeting could not start. Restart Speakeasy, then try again.', action: null };
  }
  if (rec.mode === 'ready' && rec.processingError) {
    return { text: 'Meeting could not finish normally. Check saved meetings before trying again.', action: null };
  }
  return null;
}
```
(Before writing `microphoneFailureText`'s fallback, read `STATUS.mic_failed.text` in `frontend/src/dock/App.tsx` and use that exact string.)

`TodayView.tsx` — new props `recording: RecordingInfo`, `elapsedText: string`, `nowMs: number`, `onStartMeeting()`, `onOpenNotes()`, `onRetryMicrophone()`; drop `nowMinutes` and the now-line. Render, inside the existing connected branch:
1. Header: `<h1>Today</h1>` + `ActionButton` "Start Meeting" (variant `strong`, hidden while `recording.recording || recording.processing`). The header with Start Meeting also appears in the unconnected/denied states.
2. `engineBanner(recording)` when non-null: a quiet banner row (reuse `LibraryBanner`'s styles or a `.banner` class with `--surface-2` fill and `--text` text) with a "Retry Microphone" `ActionButton` when `action === 'retry'`.
3. Hero card (`.hero`, opaque surface, 12 px radius, no nested card): when recording → "● Recording · {title ?? 'Untitled meeting'} · {elapsedText}" with "Open notes" button (`onOpenNotes`); else when `split.hero` → title (serif 20 px, one line, ellipsis), meta line `{heroWhen} · {people}`, and the existing `statusCell` (Record / Recorded ✓) on the right; else when no events at all → existing "Nothing on your calendar today." empty state; else → "Nothing else today".
4. `split.next` rows using the existing row markup.
5. When `split.more.length`: a text button "Show all {n} events" (n = next + more count) toggling `showAll`; when open render `split.more` rows and the button reads "Show fewer".
6. When `split.earlier.length`: a disclosure button "Earlier today · {n} meetings, {m} recorded" toggling `showEarlier`, rendering `split.earlier` rows when open.
7. Upcoming days: unchanged.
CSS: hero title `600 20px/1.3 var(--serif)`, meta `--text-mid`, rows unchanged; Record keeps `--accent`; no literal colours.

`App.tsx`: pass `recording`, `nowMs` (from the existing minute tick: `Date.now()`), `elapsedText` (from `recording.startedAt`, formatted `mm:ss`/`h:mm:ss`), `onStartMeeting={() => bridge.call('meeting.start')}`, `onOpenNotes={onSelectRecording}`, `onRetryMicrophone={() => bridge.call('meeting.retryMicrophone')}` (non-embedded: `console.log`). Extend the mock: `MOCK_AGENDA` gets `start`/`end` ISO values and grows to 9 events around `MOCK_NOW_MINUTES` so the mock shows hero, 3 next, "Show all", and an earlier fold; add mock states `today-recording` and `today-micfailed` (to `TODAY_STATES`) that set `recording` accordingly.

- [ ] **Step 4: Run tests and build** — `.venv/bin/python -m pytest -q tests/test_calendar_payloads.py tests/test_meetings_bridge.py tests/test_frontend_tokens.py`, `npm --prefix frontend test`, `npm --prefix frontend run build` → PASS.

- [ ] **Step 5: Visual check (reviewer, mock dev server)** — start `npm --prefix frontend run dev -- --port 5199 --strictPort` in the background, open `http://localhost:5199/meetings.html?state=today`, `?state=today-recording`, `?state=today-micfailed` in the browser pane, light and dark; run `frontend/scripts/overflow-check.js` at 820 × 520 and 1440 × 900 (must return `[]`); stop the server. Record the result.

- [ ] **Step 6: Commit** — `git add speakeasy frontend tests && git commit -m "Today: Now/Up next card, next three, folded earlier and more, engine banners"`

---

### Task 8: Remove the Dock window; Meetings is the app's window

**Files:**
- Delete: `speakeasy/ui/main_window.py`, `frontend/src/dock/` (all), `frontend/dock.html`, `frontend/src/components/Waveform.tsx` (+ its CSS module, if any, and only if nothing else imports it), `tests/test_main_window_events.py`
- Modify: `speakeasy/ui/menubar.py` (AppDelegate: no `main_window`; launch and reopen call `self.controller.open_meetings(None)`; drop `dock_window` and its `_push_state` call in `heartbeat_`; profile-switch refresh no longer pokes the dock)
- Modify: `speakeasy/ui/window_sizes.py` (drop `"dock"`), `tests/test_window_sizes.py`
- Modify: `speakeasy/ui/webbridge.py` (delete `capture_application_options` if unused), `tests/test_webbridge.py` (delete its tests)
- Modify: `frontend/vite.config.ts` (drop `dock` input), `frontend/index.html` (drop the Dock link), `scripts/build_app.sh` (lines 79–80, 140: `dock.html` → `meetings.html`), `scripts/write_build_manifest.py` (drop `'dock'`), `packaging/Speakeasy.spec` (drop `speakeasy.ui.main_window` hidden import; add `speakeasy.ui.pill_controller`, `speakeasy.ui.pill_panel`, `speakeasy.ui.next_up_view`, `speakeasy.ui.login_item` if hidden imports are listed explicitly; update the line-108 comment), `scripts/check_quit_and_cancel.py` (`cancel_handler`)
- Modify: `speakeasy/engine.py` — **comment only** at line 737 (`main_window.show()` → `Meetings window`); and `tests/test_engine_meeting.py:1100` comment. No code change.
- Modify: `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: `open_meetings` (Task 2), `PillController` (Task 5).

- [ ] **Step 1: Write the failing test** — `tests/test_no_dock_window.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest -q tests/test_no_dock_window.py` → FAIL.

- [ ] **Step 3: Implement** the deletions and edits listed above. `check_quit_and_cancel.cancel_handler` becomes:
```python
def cancel_handler(engine):
    """The real pill Discard path, bound to engine."""
    from speakeasy.ui import pill_controller
    pill_controller._make_panel = lambda target: SimpleNamespace(
        render=lambda v: None, show=lambda o: None, hide=lambda: None,
        frame_origin=lambda: None, set_drawer=lambda rows: None,
        is_drawer_visible=lambda: False, event_menu=lambda items: None)
    pill = pill_controller.PillController.alloc().initWithEngine_opener_(engine, lambda view: None)

    def handler(params, respond):
        pill.machine.phase = "confirm_discard"
        pill.pillDiscard_(None)
        respond(True)
    return handler
```
(Read how the script calls the handler first and keep its call signature.)
`AppDelegate.applicationShouldHandleReopen_hasVisibleWindows_`: `if not has_visible_windows: self.controller.open_meetings(None)`; launch: replace `main_window.show()` with `self.controller.open_meetings(None)`. Docs: in `AGENTS.md` replace the Dock-window paragraph (around lines 283–294) with the new model — the status item menu (with Next-up) is primary; the Meetings window is the Dock-reachable window (launch and Dock click); recordings show the native non-activating pill (`ui/pill_panel.py` / `ui/pill_controller.py`, pure rules in `ui/pill_model.py`); the pill's Cancel has the Discard/Keep confirm; update "The four windows (Dock, meetings, training, and microphone check)" to three; line 48 "the Dock can instead select one eligible application process" → the engine option still exists but no UI sets it. README: describe the pill, drawer, Today home and menu; remove Dock-window instructions.

- [ ] **Step 4: Full suite and build** — `.venv/bin/python -m pytest -q` (`timeout: 600000`), `npm --prefix frontend test`, `npm --prefix frontend run build`, `bash -n scripts/build_app.sh` → all pass.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "Remove the Dock window: Meetings opens on launch and Dock click; docs"`

---

### Task 9: Built-app evaluation (data level) and the user's on-screen checks

**Files:**
- Modify: this plan's Execution notes only.

- [ ] **Step 1: Build** — in the worktree: `scripts/build_app.sh` (no `--install`; needs the `models` symlink; `timeout: 600000`).
- [ ] **Step 2: Data-level run (Opus evaluator)** with a temp HOME via a Python script using `subprocess.run(env={"HOME": tmp, ...})` — never the real App Support. Check and record: the built app launches and stays up (process alive after 15 s; `ps`); the temp HOME's `settings.json` has no `pill_origin` (nothing moved it); `Speakeasy --mcp` over stdio still lists its tools; `scripts/check_quit_and_cancel.py` (real models, temp HOME, synthetic `say` audio; it now drives the pill's Discard path) passes. Starting a meeting needs a click, so recording through the UI is left to the user's checks. Note in the record that the pill, drawer, menu card and Today were **not seen on screen** by the agent.
- [ ] **Step 3: Hand the on-screen checks to the user** — post this list (and record whether they ran it or waived it):
  1. Launch `dist/Speakeasy.app` with a test HOME: `mkdir -p /tmp/se-pill-home && HOME=/tmp/se-pill-home dist/Speakeasy.app/Contents/MacOS/Speakeasy` in Terminal.app. The Meetings window opens on Today.
  2. Menu-bar icon: the menu shows Start Meeting, Open Meetings, Train My Voice…, Check Microphone…, Settings…, Quit; with Calendar connected and an event today, the Next-up card shows with a coral Record.
  3. Start Meeting: the pill appears top centre within a second; the timer ticks; the dot goes green once audio plays (`say "testing one two three"`). Typing in another app is not interrupted when the pill appears or is clicked.
  4. Click **Notes**: Meetings opens on Recording now. Drag the pill; quit and relaunch; it returns to the same spot.
  5. Click the pill's title: the drawer shows Event / Microphone / System audio / Dictation and the footer; Link to event… lists today's events.
  6. Click ■: the pill shows Processing with Cancel; then "✓ Saved · Open" for 6 s; Open opens the meeting.
  7. Light and dark: pill, drawer and menu card are legible in both.
  8. Quit; `rm -rf /tmp/se-pill-home`.
- [ ] **Step 4: Commit** the Execution notes.

---

## Execution notes

Executed 3 Oct 2026, subagent-driven, in `.claude/worktrees/recording-pill` (branch `recording-pill`, base 23f142b). Every implementer was Sonnet 5.5; every reviewer, re-reviewer, the final reviewer and the Task 9 evaluator were Opus 5.5. Each task needed exactly one fix round. Fix-round findings were mostly test gaps that the mutation checks exposed.

Baseline: build OK; pytest 1065 passed. Final: pytest **1182 passed**; node tests **31 passed**; build OK.

| Task | Commits | Review (mutations caught / survived first pass) | Fix round 1 |
|---|---|---|---|
| 1 Default options + settings | 9154bc7..02954f9 | 3 / 1 (start paths not proven to use default_options) | 3 new tests; all caught |
| 2 Navigation hand-off | 643111f..e3f9943 | 3 / 1 (takeNavigation registration) | dispatcher tests; caught |
| 3 Pill model | 67631fb..00b9b6c | 3 / 2 (mid-meeting pause hid the pill; cancel+cleanup_failed) | pause guard + tests; 3 caught |
| 4 Pill panels | dfc8da3..6821d9e | 3 / 7 (focus test vacuous; hide; drag) | tests + hitTest_ fix; 5 caught |
| 5 Pill controller | 9edaf51..20668e3 | 5 / 5 (timers; re-show) | timer/re-show tests; 5 caught |
| 6 Menu + Next-up | 3d7252b..e1578f5 | 4 / 3 (menu wiring) | menubar tests, midnight + 60-min fixes; 5 caught |
| 7 Today | ed606e1..ca38219 | 6 / 2 (retryMicrophone registration) | test + Processing hero; 2 caught |
| 8 Remove Dock window | 788ac43..402f696 | 1 / 2 (launch/reopen; spec hidden imports) | tests, README, tokens, comments; 3 caught |
| Final review fixes | 13d3f75 | 0 Critical, 4 Important, 7 Minor | engine→pill fan-out test, start error in menu, hero Record, README/AGENTS; 4 caught |

Task 7 visual check (controller, mock dev server in the browser pane): `today`, `today-recording` and `today-micfailed` in dark mode, and `today` in light mode with both folds open. The layout was as specified. The overflow check returned `[]` at 820 × 520 and 1440 × 900 for all three states. The processing hero and the hero Record button (both added later) were checked by reading the code only.

Task 9 (Opus evaluator, temp HOME):
- Build OK (no `--install`).
- The app stayed up for 15 s and exited cleanly on SIGTERM; lsof showed nothing open under the real App Support.
- No `pill_origin` (no settings.json was written).
- `--mcp` listed 11 tools.
- `check_quit_and_cancel.py` reported ALL PASS in 87.5 s: keep saved 1 meeting; the discard child was gone in 0.08 s and nothing was saved; quit took 0.005 s with exit code 0. It needs `HF_HOME=~/.cache/huggingface` under a temp HOME.
- The pill, drawer, menu card and Today were **not seen on screen** by any agent.
- Step 3 (the user's on-screen checks): run 1 on 3 Oct 2026 in Terminal.app with a temp HOME.
  - Without Calendar, Meetings opens on All meetings. That is per the spec: it opens on Today only when Calendar is connected.
  - The pill and drawer worked. The drawer opened by itself on the amber "no sound yet" warning, as designed.
  - The user found three issues, fixed in 01dc494..41f87af:
    1. **Bug, already on master:** the notepad never saved from the app ("Couldn't save — retrying"). WKWebView sends JS integers as floats, and `_check_stamps` rejected line `0.0`. Integral floats are now accepted.
    2. **Change:** the drawer is now the pill's width (360 pt) and aligned with it.
    3. **Change:** notes show and create no time stamps. Old stamp data is kept.
  - Sonnet implemented; Opus reviewed with mutations. Suite: 1192 passed, node 33 passed. Rebuilt.
  - Run 2 (the rebuilt app, user, 3 Oct 2026): **passed**.
    - The pill returned to its saved spot, and the drawer matches the pill's width.
    - Notes saved with formatting and without time stamps, and appeared on the saved meeting's Notes tab.
    - Stop → Processing → Saved · Open worked; Settings and light/dark were checked.
    - The user approved the merge.

## Follow-ups (found during the on-screen checks; not part of this branch)

- **Search ignores titles and dates.** Searching "meeting" or "Oct 3" finds nothing, even for "Test Meeting — Oct 3, 7:58 PM". `MeetingLibrary.search` queries only the transcript and notes FTS, never meeting titles or dates. This predates the branch. It needs a brainstorm: what should match, and how should results rank?
- **The light-mode sidebar looks bad** ("gray side bar looks really bad", user, 3 Oct 2026). The sidebar is unchanged by this branch. It needs a design pass on the Quiet Library light sidebar.

Rulings (decided by the controller; cost if wrong):
1. Worktree made with EnterWorktree, the branch renamed, and `.venv`/`node_modules` symlinked instead of `npm ci`. Cost: a dependency mismatch would show up as a build failure.
2. Comment-only edits allowed in engine.py (the Dock references at ~175, ~737, ~822, ~943) and in tests/test_engine_meeting.py. Cost: comment lines in engine.py.
3. Fix rounds 4–5 would stay on Sonnet (never used). Cost: none.
4. PillMachine ignores `paused` during a meeting phase, because `engine.pause()` (the training window) sets PAUSED mid-meeting. mic_failed/mic_recovering/loading still end a recording. Cost: if the engine ever ended a meeting on PAUSED, the pill would linger until the next state.
5. Parked: the drawer says "All apps" even when `capture_scope == "selected"`, because no UI sets single-app capture any more. Cost: a wrong label if a future UI brings it back.
6. next_up shows an in-progress event that started yesterday, and rounds minutes before the 60-minute cutoff, so it never says "Starts in 60 min". Cost: two lines differ from the plan's code.
7. The duplicated microphoneFailureText resolved itself when the dock was deleted. Cost: none.
8. "Earlier today · 1 meeting" uses the singular. Cost: a one-word copy difference from the plan.
9. The final review ran before the Task 9 build. Cost: none.
10. The Today hero shows Record for a later event (status 'none'), matching the menu's Next-up card. Cost: an early recording can be linked to a far-off event.
11. A Dock click always opens Meetings, because the pill panel may count as a visible window. Cost: none beyond bringing Meetings forward.
12. Parked: `meeting_start_error` persists until the next meeting start, and it outranks the "Text retained for 60 seconds" hint in the menu status line. This is the same precedence `meeting_processing_error` already had before this branch. Cost: after a failed meeting start, a later failed paste's recovery hint is hidden in the menu until the next meeting starts.

Deferred minors, all triaged "fine to leave" by the final reviewer:
- the Switch control has no disabled style;
- App-level navigation edge cases (a failed recording.get loses the request; search text is not cleared; another sheet may already be open);
- MeetingLibrary() is opened on each menu open;
- the drawer is rebuilt every second while open;
- pill_origin is stored in global coordinates, not screen-relative;
- Today rows can be stale for up to 30 s around a recording's start or stop;
- Start Meeting gives no feedback when the engine refuses it;
- some checks are source-text tests only.
