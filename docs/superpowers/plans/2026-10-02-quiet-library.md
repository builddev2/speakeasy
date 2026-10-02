# Quiet Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Meetings window calm and readable in both light and dark mode, and give the native record banner a one-click way to turn its prompts off.

**Architecture:** One CSS token set in `frontend/src/styles/tokens.css` (dark by default, redefined for `prefers-color-scheme: light`), with no literal colours left in CSS modules. The app's appearance (System / Light / Dark) is a setting applied once with `NSApp.setAppearance_`, which WKWebView and the native banner both follow. The Meetings React app moves from three columns to two, the detail pane becomes one readable column with a top toolbar, and the transcript is grouped into speaker turns by a pure helper. The banner refresh stays in PyObjC (`record_prompt_panel.py`) and adds a ⋯ menu, an Undo notice and dynamic colours.

**Tech Stack:** React 18 + TypeScript + Vite (CSS modules), PyObjC/AppKit, pytest, Node 24's built-in `node --test` (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-10-02-quiet-library-design.md`
(review page: <https://claude.ai/artifact/UeZzqUHKXVULJJW7NeoaaG>)

## Execution roles (user requirement)

- **Models are fixed per role. Set `model` explicitly on every Agent dispatch; never rely on the default, which inherits the controller's model.**

  | Role | Model | Agent `model` value | Covers |
  |---|---|---|---|
  | Implementer (writes code and tests) | Sonnet 5.5 (`claude-sonnet-5-5`) | `"sonnet"` | Every task's Steps, including fixes after a review |
  | Task reviewer / eval | Opus 5.5 (`claude-opus-5-5`) | `"opus"` | Spec compliance and code-quality review after each task, mutation checks, the visual and overflow checks |
  | Final whole-branch review | Opus 5.5 | `"opus"` | One review of the complete branch before merge |
  | Controller (dispatches, merges, updates this plan) | The session model (Opus) | — | Does not write feature code itself |

- Implementer briefs carry the exact values from this plan (sizes, colours, copy strings, test code) and the instruction to give long Bash calls `timeout: 600000`.
- Reviewers verify by **mutation**: break the code, confirm a test fails, restore. A review that ran no mutation is not a pass.
- If a Sonnet implementer is stuck after two attempts on the same step, the controller re-dispatches with a sharper brief on Sonnet; it does not switch the implementer to Opus without asking the user.
- Record the model used for each implementer and reviewer dispatch in Execution notes (one line per task).
- Mutation runs use `.venv/bin/python -B -m pytest` after clearing `__pycache__` (memory: mutation-review gotchas).
- Never run the app on real App Support data from a subagent; use a temp `HOME` (conftest already isolates pytest).

## Setup (once, before Task 1)

- [ ] Create a worktree/branch from current master: `git worktree add .claude/worktrees/quiet-library -b quiet-library master` (or `EnterWorktree`, then `git merge --ff-only master`, because `EnterWorktree` branches from origin/master).
- [ ] In the worktree: symlink `.venv` and `models` from the main checkout (`ln -s ../../../.venv .venv`, `ln -s ../../../models models`), then `npm --prefix frontend ci`.
- [ ] Baseline: `npm --prefix frontend run build` passes and `.venv/bin/python -m pytest -q` passes (about 1004 tests). **Give the Bash call `timeout: 600000`** so the suite is never backgrounded.

## Global Constraints

- Fully offline; no new runtime or build dependencies (no vitest/jest). Frontend unit tests: `node --test` on `.ts` files (Node ≥ 23 strips types). Pure helpers under test may use only `import type` plus imports of other pure `.ts` files with explicit `.ts` extensions.
- No literal colours (`#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb(`, `rgba(`, `hsl(`) in any `frontend/src/**/*.module.css`; all colours come from `var(--…)` tokens in `tokens.css`.
- Contrast (WCAG 2.x formula, alpha composited onto the surface): `--text-hi`, `--text`, `--text-mid` ≥ 4.5:1 and `--text-lo` ≥ 3.0:1 on `--surface-content` and `--surface-elevated`; `--on-accent` on `--accent` ≥ 4.5:1; `--on-rec` on `--rec` ≥ 4.5:1; every `--sp-N` ≥ 4.5:1 on `--surface-content`. In both modes.
- Coral (`--accent`) is used only for Record actions and the focus ring. Selection uses `--selection`.
- Appearance setting: `settings.json` key `"appearance"`, values `"system" | "light" | "dark"`, default and fallback `"system"`.
- `WebWindow` must not force `NSAppearanceNameDarkAqua`.
- Record banner: stays 360 × 92 pt, 12 pt margin, non-activating, never becomes key; `CONFIRM_SECONDS` stays 2.0; new `NOTICE_SECONDS = 5.0`. No `makeKeyAndOrderFront` or `activateIgnoringOtherApps` in `record_prompt_panel.py` / `record_prompt_controller.py`.
- Call detection privacy: the banner never names the calling app; no per-app mute.
- Turning prompts off never calls `begin_meeting` or `end_meeting`.
- Do not edit `speakeasy/ui/menubar.py`, `speakeasy/engine.py` or any dictation/insertion code (an uncommitted Codex worktree edits menubar/engine).
- Every window is resizable with the minimums in the spec's *Resizable windows* table; nothing clips from the minimum up to 2560 × 1440 in either appearance. The banner stays fixed-size and every button title fits.
- Copy strings, verbatim: "Turn off calendar prompts", "Turn off call prompts", "Calendar prompts are off", "Call prompts are off", "Turn back on in Meetings › Settings", "Undo", "Record prompts", "Appearance", "System", "Light", "Dark".

## Review Focus

1. **Appearance changed while windows are open** (Meetings, Dock and banner all visible): every window and the banner switch immediately, without reopening. Test: Task 3 asserts `NSApp.appearance()` changes on `settings.meetings.set` with `appearance`, and `WebWindow` source no longer sets an appearance.
2. **"System" with macOS switching light↔dark on its own** (Auto appearance at sunset): pages follow via `prefers-color-scheme`; no page caches a theme in JS. Test: Task 2's lint test also fails if any `.tsx` reads `matchMedia('(prefers-color-scheme`.
3. **Banner turned off during a recording** (call-ended banner showing): the recording keeps going. Test: Task 8 `test_turning_off_call_prompts_during_recording_never_ends_it`.
4. **Long titles beside the new ⋯ button and in sidebar rows**: the title truncates with an ellipsis and never overlaps ⋯; sidebar times never truncate. Test: Task 8 asserts the title frame's max-X ≤ the ⋯ button's min-X − 4; Task 7 sets `flex-shrink: 0` on the time and checks it in the mock preview with the long "Design Review — Onboarding Flow" row.
5. **Find-in-transcript and jump-to-search-result after lines merge into turns**: the highlight and scroll land on the exact segment, and speaker rename opens from the turn's name. Test: Task 6 node test asserts every input segment appears exactly once, in order, inside the turns; Task 6 Step 7 checks `?state=popover` and a search jump in the mock.

---

### Task 1: Light and dark tokens with a contrast test

**Files:**
- Modify: `frontend/src/styles/tokens.css:1-60` (the `:root` block)
- Create: `tests/test_frontend_tokens.py`

**Interfaces:**
- Produces tokens (both modes): `--accent`, `--on-accent`, `--rec`, `--on-rec`, `--surface-content`, `--selection`, plus every existing token (`--coral-1`, `--coral-2`, `--red-1`, `--red-2`, `--green`, `--rec-red`, `--amber`, `--text-hi`, `--text`, `--text-mid`, `--text-lo`, `--text-xlo`, `--glass-*`, `--hairline`, `--hairline-lo`, `--surface-elevated`, `--scrim`, `--surface-sticky`, `--ring-on-glass`, `--sp-1`…`--sp-7`, `--shadow-panel`). `--text-on-accent` and `--text-on-accent-mid` are removed in Task 2 (kept here as aliases: `--text-on-accent: var(--on-accent); --text-on-accent-mid: var(--on-accent);`).
- Produces `tests/test_frontend_tokens.py::parse_tokens() -> tuple[dict[str,str], dict[str,str]]` (dark, light), reused by Task 2.

- [ ] **Step 1: Write the failing test** — `tests/test_frontend_tokens.py`:

```python
"""Frontend colour tokens: both appearances define every token, and the
text/button pairs meet WCAG contrast. Pure file parsing; no browser."""

import re
from pathlib import Path

TOKENS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles" / "tokens.css"


def _block(css: str, start: int) -> str:
    depth, i = 0, css.index("{", start)
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[i + 1:j]
    raise ValueError("unbalanced braces")


def _decls(block: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block)}


def parse_tokens():
    css = re.sub(r"/\*.*?\*/", "", TOKENS.read_text(), flags=re.S)
    dark = _decls(_block(css, css.index(":root")))
    media = css.index("@media (prefers-color-scheme: light)")
    inner = _block(css, media)
    light = _decls(_block(inner, inner.index(":root")))
    return dark, {**dark, **light}


def _rgba(value: str):
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", value)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                float(m.group(4)) if m.group(4) else 1.0)
    raise ValueError(f"not a plain colour: {value}")


def _over(fg, bg):
    a = fg[3]
    return tuple(a * f + (1 - a) * b for f, b in zip(fg[:3], bg[:3]))


def _lum(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(fg: str, bg: str) -> float:
    bg_rgb = _rgba(bg)[:3]
    a, b = _lum(_over(_rgba(fg), bg_rgb)), _lum(bg_rgb)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_light_mode_redefines_every_colour_token():
    dark, light_only = parse_tokens()
    css = TOKENS.read_text()
    inner = _block(css, css.index("@media (prefers-color-scheme: light)"))
    redefined = set(_decls(inner))
    skip = {"--sans", "--mono", "--radius", "--text-on-accent", "--text-on-accent-mid"}
    missing = sorted(set(dark) - redefined - skip)
    assert missing == []


def test_text_and_button_contrast_in_both_modes():
    dark, light = parse_tokens()
    for name, t in (("dark", dark), ("light", light)):
        for surface in ("--surface-content", "--surface-elevated"):
            for tok, floor in (("--text-hi", 4.5), ("--text", 4.5), ("--text-mid", 4.5), ("--text-lo", 3.0)):
                ratio = contrast(t[tok], t[surface])
                assert ratio >= floor, f"{name} {tok} on {surface}: {ratio:.2f}"
        assert contrast(t["--on-accent"], t["--accent"]) >= 4.5, name
        assert contrast(t["--on-rec"], t["--rec"]) >= 4.5, name
        for n in range(1, 8):
            ratio = contrast(t[f"--sp-{n}"], t["--surface-content"])
            assert ratio >= 4.5, f"{name} --sp-{n}: {ratio:.2f}"


def test_pinned_accent_values():
    dark, light = parse_tokens()
    assert (dark["--accent"].upper(), dark["--on-accent"].upper()) == ("#E8955A", "#1D1F1E")
    assert (light["--accent"].upper(), light["--on-accent"].upper()) == ("#B8551F", "#FFFFFF")
    assert (dark["--rec"].upper(), light["--rec"].upper()) == ("#FF5A4E", "#D33A2F")
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q`
Expected: FAIL — `ValueError: substring not found` (no light media block yet).

- [ ] **Step 3: Rewrite the `:root` block of `tokens.css` and add the light block**

Keep everything below the current `:root { … }` (the `* {}`, `html, body`, `body::before`, `#root`, `@keyframes pill`, `html.embedded` rules) unchanged except where noted. Replace the `:root` block with:

```css
/* Dark is the default appearance; the light block below redefines every
   colour. The page follows the window's appearance through
   prefers-color-scheme (set by NSApp.setAppearance_ in speakeasy/ui/appearance.py). */
:root {
  color-scheme: dark;
  /* Brand */
  --coral-1: #E8955A;
  --coral-2: #D97844;
  --red-1:   #FF5A4E;
  --red-2:   #E23B32;

  /* Record actions and focus: coral; stop and live: red */
  --accent:    #E8955A;
  --on-accent: #1D1F1E;
  --rec:       #FF5A4E;
  --on-rec:    #1D1F1E;

  /* Semantic status */
  --green:   #3DDC84;
  --rec-red: #FF453A;
  --amber:   #F0B34A;

  /* Text */
  --text-hi: rgba(255,255,255,0.97);
  --text:    rgba(255,255,255,0.88);
  --text-mid:rgba(255,255,255,0.62);
  --text-lo: rgba(255,255,255,0.40);
  --text-xlo:rgba(255,255,255,0.32);

  /* Solid content surface (summary, transcript) and neutral selection */
  --surface-content: #1C1E1D;
  --selection: rgba(255,255,255,0.08);

  /* Glass surfaces (navigation layer) */
  --glass-panel: linear-gradient(145deg, rgba(255,255,255,0.055), rgba(18,21,22,0.12));
  --glass-window: linear-gradient(145deg, rgba(255,255,255,0.0375) 0%, rgba(19,22,23,0.05) 46%, rgba(8,10,11,0.08) 100%);
  --glass-pane: rgba(7,9,10,0.11);
  --glass-sidebar: rgba(255,255,255,0.0125);
  --glass-fill:  rgba(255,255,255,0.08);
  --glass-fill-2:rgba(255,255,255,0.05);
  --hairline:    rgba(255,255,255,0.16);
  --hairline-lo: rgba(255,255,255,0.10);

  --surface-elevated: #262A28;
  --scrim: rgba(0,0,0,0.5);
  --surface-sticky: rgba(12,15,14,0.82);
  --ring-on-glass: rgba(12,15,14,0.9);
  --text-on-accent: var(--on-accent);
  --text-on-accent-mid: var(--on-accent);

  /* Speaker palette (transcript colour-coding) */
  --sp-1:#6FD8B0; --sp-2:#F0B34A; --sp-3:#C08CF2; --sp-4:#6EA8FF;
  --sp-5:#7ED97E; --sp-6:#F0895A; --sp-7:#DE8FB4;

  /* Type */
  --sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif;
  --serif: ui-serif, "New York", Georgia, serif;
  --mono: ui-monospace, "SF Mono", "SFMono-Regular", Menlo, monospace;

  /* Elevation */
  --shadow-panel: inset 0 1px 0 rgba(255,255,255,0.14),
                  0 24px 60px -18px rgba(0,0,0,0.65),
                  0 3px 12px rgba(0,0,0,0.4);
  --radius: 16px;
}

@media (prefers-color-scheme: light) {
  :root {
    color-scheme: light;
    --coral-1: #B8551F;
    --coral-2: #A94B1A;
    --red-1:   #D33A2F;
    --red-2:   #B92F25;
    --accent:    #B8551F;
    --on-accent: #FFFFFF;
    --rec:       #D33A2F;
    --on-rec:    #FFFFFF;
    --green:   #1F8A4C;
    --rec-red: #D33A2F;
    --amber:   #9A6700;
    --text-hi: rgba(0,0,0,0.88);
    --text:    rgba(0,0,0,0.80);
    --text-mid:rgba(0,0,0,0.58);
    --text-lo: rgba(0,0,0,0.50);
    --text-xlo:rgba(0,0,0,0.40);
    --surface-content: #FBFBFA;
    --selection: rgba(0,0,0,0.065);
    --glass-panel: linear-gradient(145deg, rgba(255,255,255,0.55), rgba(245,247,246,0.35));
    --glass-window: linear-gradient(145deg, rgba(255,255,255,0.45) 0%, rgba(243,245,244,0.38) 46%, rgba(236,239,237,0.35) 100%);
    --glass-pane: rgba(255,255,255,0.5);
    --glass-sidebar: rgba(0,0,0,0.025);
    --glass-fill:  rgba(0,0,0,0.05);
    --glass-fill-2:rgba(0,0,0,0.035);
    --hairline:    rgba(0,0,0,0.12);
    --hairline-lo: rgba(0,0,0,0.07);
    --surface-elevated: #FFFFFF;
    --scrim: rgba(0,0,0,0.25);
    --surface-sticky: rgba(251,251,250,0.88);
    --ring-on-glass: rgba(255,255,255,0.9);
    --sp-1:#237A5B; --sp-2:#946200; --sp-3:#7D55BD; --sp-4:#2F6FD6;
    --sp-5:#357A35; --sp-6:#A94B22; --sp-7:#B04E80;
    --serif: ui-serif, "New York", Georgia, serif;
    --shadow-panel: inset 0 1px 0 rgba(255,255,255,0.7),
                    0 18px 50px -20px rgba(20,30,25,0.35),
                    0 2px 8px rgba(20,30,25,0.08);
  }
}
```

Note `--surface-elevated` changes from `rgba(38,42,40,1)` to the identical `#262A28` so the parser reads it. `--serif` is redefined only so the "every token redefined" test passes; `--sans`/`--mono`/`--radius` are skipped by name.

Also in the same file, give the dev-only backdrop a light variant (inside the same media query, after `:root`):

```css
  body { background: #EEF1EF; }
  body::before {
    background:
      radial-gradient(closest-side at 22% 26%, rgba(232,149,90,0.30), transparent 70%),
      radial-gradient(closest-side at 82% 18%, rgba(86,150,255,0.25), transparent 70%),
      linear-gradient(160deg, #F4F6F5, #E6EAE8 70%);
  }
```

(The `html.embedded` rules that follow still hide it in the app.)

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q` → PASS (3 passed).
Run: `npm --prefix frontend run build` → succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles/tokens.css tests/test_frontend_tokens.py
git commit -m "Tokens: light and dark sets with a WCAG contrast test"
```

---

### Task 2: No literal colours in CSS modules

**Files:**
- Modify: every `frontend/src/**/*.module.css` that contains a literal colour. Current counts: `dock/App.module.css` 24, `training/App.module.css` 21, `components/ActionButton` 9, `PrimaryButton` 7, `StatusDot` 6, `GlassPanel` 6, `GlassButton` 6, `TitleBar` 5, `AppIdentity` 5, `Waveform` 4, `meetings/TodayView` 2, `meetings/MeetingList` 2, `diagnostic/App` 2, `meetings/Sidebar` 1, `meetings/SearchResults` 1, `meetings/MeetingDetail` 1, `meetings/LibraryBanner` 1.
- Modify: `frontend/src/styles/tokens.css` (new role tokens, both blocks)
- Modify: `tests/test_frontend_tokens.py` (lint tests)

**Interfaces:**
- Consumes: `parse_tokens()` and the tokens from Task 1.
- Produces: no `--text-on-accent`/`--text-on-accent-mid` tokens; usages replaced with `--on-accent`.

- [ ] **Step 1: Add the failing lint tests** (append to `tests/test_frontend_tokens.py`):

```python
SRC = TOKENS.parents[1]
LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def test_css_modules_use_tokens_only():
    offenders = []
    for path in sorted(SRC.rglob("*.module.css")):
        text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
        for n, line in enumerate(text.splitlines(), 1):
            if LITERAL.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    assert offenders == []


def test_no_removed_tokens_and_no_js_theme_detection():
    for path in SRC.rglob("*"):
        if path.suffix not in {".css", ".tsx", ".ts"} or path.name == "tokens.css":
            continue
        text = path.read_text()
        assert "--text-on-accent" not in text, path
        assert "prefers-color-scheme" not in text, path   # CSS-only theming lives in tokens.css
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q`
Expected: FAIL listing about 103 lines.

- [ ] **Step 3: Replace literals with tokens**

For each offending line:
1. If the literal equals an existing dark token value (e.g. `rgba(255,255,255,0.08)` → `var(--glass-fill)`, `#E8955A` → `var(--coral-1)`, `#FF453A` → `var(--rec-red)`), use that token.
2. Otherwise add a role-named token to **both** blocks of `tokens.css` (dark value = the current literal; light value = the same role on a light ground, i.e. black-based alpha where the dark one is white-based, and white-based where the dark one is black-based). Name by role and component, e.g. `--dock-waveform-1`, `--titlebar-close`, `--button-strong-bg`. Traffic-light colours (`#FF5F57`, `#FEBC2E`, `#28C840`) are the same in both modes.
3. `color: var(--text-on-accent)` / `var(--text-on-accent-mid)` → `var(--on-accent)`; then delete both alias lines from `tokens.css`.
4. `box-shadow`/`linear-gradient` with literals: move the whole value into a token (e.g. `--shadow-button`).

Then remove `"--text-on-accent", "--text-on-accent-mid"` from the `skip` set in `test_light_mode_redefines_every_colour_token`.

- [ ] **Step 4: Run tests and build**

Run: `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q` → PASS (5 passed).
Run: `npm --prefix frontend run build` → succeeds.

- [ ] **Step 5: Look at both modes** — start `npm --prefix frontend run dev -- --port 5199 --strictPort` in the background, open `http://localhost:5199/meetings.html`, `dock.html`, `training.html`, `diagnostic.html` with the browser pane, and screenshot each with `resize_window colorScheme: "dark"` then `"light"`. Dark must look like before; light must have readable text everywhere (no white text on white). Stop the dev server.

- [ ] **Step 6: Commit**

```bash
git add frontend/src tests/test_frontend_tokens.py
git commit -m "Frontend: every colour comes from a light/dark token"
```

---

### Task 3: Appearance setting applied app-wide

**Files:**
- Create: `speakeasy/ui/appearance.py`
- Modify: `speakeasy/settings.py` (after `set_meeting_settings`, ~line 160)
- Modify: `speakeasy/ui/webwindow.py:95-115` (remove the forced DarkAqua)
- Modify: `speakeasy/ui/meetings_bridge.py:209-224` (`settings_get_payload`, `settings_set_payload`)
- Modify: `frontend/src/mock/meetings.ts` (`MeetingSettings` type and `MOCK_MEETING_SETTINGS`)
- Test: `tests/test_appearance.py` (new), `tests/test_meetings_bridge.py`, `tests/test_settings.py`

**Interfaces:**
- Produces `settings.get_appearance() -> str` (`"system"|"light"|"dark"`), `settings.set_appearance(choice: str) -> str` (raises `ValueError("Appearance must be System, Light or Dark.")`).
- Produces `appearance.CHOICES = ("system", "light", "dark")`, `appearance.apply(choice: str) -> None`.
- Bridge: `settings.meetings.get` returns `appearance`; `settings.meetings.set` accepts `appearance`.
- TS: `MeetingSettings` gains `appearance: 'system' | 'light' | 'dark'`.

- [ ] **Step 1: Failing tests** — `tests/test_appearance.py`:

```python
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
```

Append to `tests/test_meetings_bridge.py` (use that file's existing bridge fixture/constructor pattern — read its first test to copy how `MeetingsBridge` is built):

```python
def test_settings_round_trip_appearance(monkeypatch):
    from speakeasy.ui import appearance
    applied = []
    monkeypatch.setattr(appearance, "apply", applied.append)
    bridge = make_bridge()                     # the file's existing helper/fixture
    assert bridge.settings_get_payload({})["appearance"] == "system"
    out = bridge.settings_set_payload({"appearance": "dark"})
    assert out["appearance"] == "dark" and applied == ["dark"]
    with pytest.raises(ValueError):
        bridge.settings_set_payload({"appearance": "neon"})
    assert applied == ["dark"]
```

(If the file has no `make_bridge` helper, construct the bridge exactly as its existing settings tests do and name the local accordingly.)

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_appearance.py tests/test_meetings_bridge.py -q`
Expected: FAIL — `ImportError: cannot import name 'appearance'`.

- [ ] **Step 3: Implement**

`speakeasy/settings.py` (after `set_meeting_settings`):

```python
# -- appearance -----------------------------------------------------------------

APPEARANCES = ("system", "light", "dark")


def get_appearance() -> str:
    """System (follow macOS), light or dark; anything unreadable is system."""
    value = _read().get("appearance")
    return value if value in APPEARANCES else "system"


def set_appearance(choice) -> str:
    if choice not in APPEARANCES:
        raise ValueError("Appearance must be System, Light or Dark.")
    data = _read()
    data["appearance"] = choice
    _write(data)
    return choice
```

`speakeasy/ui/appearance.py`:

```python
"""App-wide light/dark appearance. Set once on NSApp, so every window, its
WKWebView (via prefers-color-scheme) and the record banner follow it; None
means follow macOS. Main thread only."""

from .. import settings

CHOICES = settings.APPEARANCES


def apply(choice: str) -> None:
    from AppKit import (NSApplication, NSAppearance, NSAppearanceNameAqua,
                        NSAppearanceNameDarkAqua)

    name = {"light": NSAppearanceNameAqua, "dark": NSAppearanceNameDarkAqua}.get(choice)
    NSApplication.sharedApplication().setAppearance_(
        NSAppearance.appearanceNamed_(name) if name else None)


def apply_saved() -> None:
    apply(settings.get_appearance())
```

`speakeasy/ui/webwindow.py`: delete `NSAppearance,` and `NSAppearanceNameDarkAqua,` from the import list and replace
`window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))` with:

```python
        # The app-level appearance (System/Light/Dark) is applied once, before
        # the first window, so every window and page follow it.
        appearance.apply_saved()
```

and add `from speakeasy.ui import appearance` beside the existing `from speakeasy.ui import glass` import. (Applying on every window creation is idempotent and guarantees it runs before the Dock, the first window, appears — without touching `menubar.py`.)

`speakeasy/ui/meetings_bridge.py`:

```python
    def settings_get_payload(self, params) -> dict:
        stored = settings.get_meeting_settings()
        calendars = self._calendar.calendars if self._calendar is not None else []
        return {"offerToRecord": stored["offer_to_record"],
                "detectCalls": stored["detect_calls"],
                "appearance": settings.get_appearance(),
                "accounts": calendar_payloads.calendars_payload(
                    calendars, stored["calendar_choices"])}

    def settings_set_payload(self, params) -> dict:
        if params.get("appearance") is not None:
            from . import appearance
            appearance.apply(settings.set_appearance(params["appearance"]))
        settings.set_meeting_settings(
            offer_to_record=params.get("offerToRecord"),
            detect_calls=params.get("detectCalls"),
            calendar_choices=params.get("calendars"))
        if params.get("calendars") is not None and self._calendar is not None:
            self._calendar.request_sync()
        return self.settings_get_payload({})
```

(The bridge handler runs synchronously inside `BridgeDispatcher.dispatch`, called from the WKScriptMessageHandler on the main thread, so `apply` is main-thread safe.)

`frontend/src/mock/meetings.ts`:

```ts
export type Appearance = 'system' | 'light' | 'dark';
export interface MeetingSettings { offerToRecord: boolean; detectCalls: boolean; appearance: Appearance; accounts: CalendarAccountSettings[] }
```

and add `appearance: 'system',` to `MOCK_MEETING_SETTINGS`. Widen the `onChange` patch type in `SettingsSheet.tsx` and `App.tsx`'s `onChangeSettings` to include `appearance?: Appearance` (Task 4 adds the control).

- [ ] **Step 4: Run tests and build**

Run: `.venv/bin/python -m pytest tests/test_appearance.py tests/test_meetings_bridge.py tests/test_settings.py tests/test_webbridge.py -q` → PASS.
Run: `npm --prefix frontend run build` → succeeds.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/settings.py speakeasy/ui/appearance.py speakeasy/ui/webwindow.py speakeasy/ui/meetings_bridge.py frontend/src tests
git commit -m "Appearance: System/Light/Dark setting applied app-wide"
```

---

### Task 4: Settings sheet — Record prompts and Appearance groups

**Files:**
- Modify: `frontend/src/meetings/SettingsSheet.tsx`, `frontend/src/meetings/SettingsSheet.module.css`

**Interfaces:**
- Consumes: `MeetingSettings.appearance`, `Appearance` type (Task 3).
- `onChange(patch: { offerToRecord?: boolean; detectCalls?: boolean; appearance?: Appearance; calendars?: Record<string, boolean> })`.

- [ ] **Step 1: Restructure the sheet.** Replace the two toggle rows with:

```tsx
      <div className={styles.sectionTitle}>Record prompts</div>
      <div className={styles.toggleRow}>
        <span id="offer-to-record-label">
          Offer to record calendar meetings
          <span className={styles.hint}>When an event with other people starts</span>
        </span>
        <Switch checked={offerToRecord} onChange={(value) => onChange({ offerToRecord: value })} ariaLabelledBy="offer-to-record-label" />
      </div>
      <div className={styles.toggleRow}>
        <span id="detect-calls-label">
          Offer to record calls in other apps
          <span className={styles.hint}>Also asks to stop when the call ends</span>
        </span>
        <Switch checked={detectCalls} onChange={(value) => onChange({ detectCalls: value })} ariaLabelledBy="detect-calls-label" />
      </div>

      <div className={styles.sectionTitle}>Appearance</div>
      <div className={styles.segmented} role="radiogroup" aria-label="Appearance">
        {(['system', 'light', 'dark'] as const).map((choice) => (
          <button
            key={choice}
            role="radio"
            aria-checked={appearance === choice}
            className={appearance === choice ? `${styles.segment} ${styles.segmentOn}` : styles.segment}
            onClick={() => onChange({ appearance: choice })}
          >
            {choice === 'system' ? 'System' : choice === 'light' ? 'Light' : 'Dark'}
          </button>
        ))}
      </div>
```

with `const { offerToRecord, detectCalls, appearance, accounts } = settings;` and the doc comment updated to "record prompts, appearance, calendar checklist, export". The Calendars section and Export button stay.

- [ ] **Step 2: Styles** (append to `SettingsSheet.module.css`):

```css
.hint { display: block; margin-top: 2px; font-size: 11.5px; color: var(--text-lo); }
.segmented { display: inline-flex; padding: 2px; border-radius: 8px; background: var(--glass-fill); align-self: flex-start; }
.segment { border: 0; background: transparent; color: var(--text-mid); font: 500 12.5px var(--sans); padding: 4px 14px; border-radius: 6px; cursor: pointer; }
.segmentOn { background: var(--surface-elevated); color: var(--text-hi); box-shadow: 0 1px 2px var(--hairline); }
.segment:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
```

- [ ] **Step 3: Mock wiring.** In `App.tsx`, the mock branch of `onChangeSettings` must merge `appearance` into `meetingSettings` state like the other fields (read the function first; follow its existing pattern).

- [ ] **Step 4: Verify** — `npm --prefix frontend run build`; `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q`; open `meetings.html?state=settings` in dark and light and screenshot. Clicking Light/Dark changes the selected segment.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/meetings/SettingsSheet.tsx frontend/src/meetings/SettingsSheet.module.css frontend/src/meetings/App.tsx
git commit -m "Settings: Record prompts and Appearance groups"
```

---

### Task 5: Quiet surfaces — neutral selection, no cards, toolbar actions, time ranges

**Files:**
- Create: `frontend/src/meetings/timeRange.ts`, `frontend/tests/timeRange.test.ts`
- Modify: `frontend/package.json` (add `"test": "node --test \"tests/*.test.ts\""`)
- Modify: `frontend/src/meetings/MeetingList.module.css:50-135`, `Sidebar.module.css` (`.rowActive`), `MeetingDetail.tsx` (toolbar, emoji), `MeetingDetail.module.css` (`.segmentActive`, `.summaryPane`, `.transcriptPane`, `.body`, `.actionsRow`), `TodayView.tsx:49`

**Interfaces:**
- Produces `formatTimeRange(start: string, end: string | null): string` in `timeRange.ts`.

- [ ] **Step 1: Failing node test** — `frontend/tests/timeRange.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatTimeRange } from '../src/meetings/timeRange.ts';

test('shared meridiem appears once', () => {
  assert.equal(formatTimeRange('9:00 AM', '9:12 AM'), '9:00–9:12 AM');
  assert.equal(formatTimeRange('4:30 PM', '5:00 PM'), '4:30–5:00 PM');
});

test('different meridiems keep both', () => {
  assert.equal(formatTimeRange('11:30 AM', '12:15 PM'), '11:30 AM–12:15 PM');
});

test('no end time or unparseable input is passed through', () => {
  assert.equal(formatTimeRange('9:00 AM', null), '9:00 AM');
  assert.equal(formatTimeRange('09:00', '10:00'), '09:00–10:00');
});
```

Add to `frontend/package.json` scripts: `"test": "node --test \"tests/*.test.ts\""` (a bare directory argument fails on Node 24; the glob form works — checked 2 Oct 2026).

- [ ] **Step 2: Run** — `npm --prefix frontend test` → FAIL (`Cannot find module …/timeRange.ts`).

- [ ] **Step 3: Implement** `frontend/src/meetings/timeRange.ts`:

```ts
const CLOCK = /^(\d{1,2}:\d{2})\s?(AM|PM)$/;

/** "9:00–9:12 AM" when both ends share AM/PM, else both meridiems. */
export function formatTimeRange(start: string, end: string | null): string {
  if (!end) return start;
  const a = CLOCK.exec(start);
  const b = CLOCK.exec(end);
  if (a && b && a[2] === b[2]) return `${a[1]}–${b[1]} ${b[2]}`;
  return `${start}–${end}`;
}
```

In `TodayView.tsx` replace the body of `timeRange(event)` with `return formatTimeRange(event.time, event.endTime);` and import it. Run `npm --prefix frontend test` → PASS.

- [ ] **Step 4: Neutral selection.** In `MeetingList.module.css`: `.rowActive { background: var(--selection); }`; delete the `.rowActive .rowTitleLine`, `.rowActive .time`, `.rowActive .duration`, `.rowActive .summaryDot`, `.rowActive .sub` overrides (text keeps its normal colours); add `.rowActive .title { font-weight: 600; }`. Apply the same `background: var(--selection)` to `.rowActive` in `Sidebar.module.css` and `.segmentActive` in `MeetingDetail.module.css` (keep `color: var(--text-hi)`). Focus outlines that use `--coral-1` switch to `--accent`.

- [ ] **Step 5: Content on a solid surface, no inner cards.** In `MeetingDetail.module.css`:

```css
.detail { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; background: var(--surface-content); }
.body { flex: 1; min-height: 0; overflow-y: auto; padding: 28px 32px 32px; }
.bodyInner { max-width: 640px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
.summaryPane, .transcriptPane { margin-top: 4px; border: 0; border-radius: 0; background: transparent; padding: 0; overflow: visible; }
.title { font: 600 26px/1.2 var(--serif); color: var(--text-hi); }
```

Wrap the children of `.body` in `<div className={styles.bodyInner}>`. The summary/transcript panes stop scrolling on their own; `.body` scrolls. (`.title` is the existing title class; if the file names it differently, apply the font to that class.)

- [ ] **Step 6: Toolbar actions.** Move the Summary/Transcript segmented control and the action buttons into `.toolbar` at the top: `[segmented] [spacer] [Copy] [Export] [⋯]`. Delete the bottom `.actionsRow` block and its CSS. Copy and Export become icon buttons (`aria-label="Copy"`, `aria-label="Export"`, `title` the same) using inline SVG strokes `currentColor`:

```tsx
<button className={styles.iconButton} aria-label="Copy" title="Copy" onClick={onCopy}>
  <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="8.5" height="8.5" rx="1.5"/><path d="M3 10.5V3.5A1 1 0 0 1 4 2.5h6.5"/></svg>
</button>
<button className={styles.iconButton} aria-label="Export" title="Export" onClick={onExport}>
  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 10V2.5M5 5.2 8 2.3l3 2.9M3 9v4.5h10V9"/></svg>
</button>
```

```css
.iconButton { width: 28px; height: 26px; display: grid; place-items: center; border: 0; border-radius: 7px; background: transparent; color: var(--text-mid); cursor: pointer; }
.iconButton:hover { background: var(--glass-fill); color: var(--text-hi); }
.iconButton:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.iconButton svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.6; }
```

The ⋯ menu (Rename…, Redo summary, Delete…) keeps its items and moves into the toolbar; its popup opens downward (`top: 100%` instead of `bottom: …`). The search input that lived in `.toolbar` is moved by Task 7; in this task leave it at the toolbar's right end, after ⋯. The segmented control renders only when `detail` is set.

- [ ] **Step 7: Remove the emoji.** In `MeetingDetail.tsx` (~line 478) delete the `📅` span from the event chip; keep the chip text. Use the same calendar SVG as the banner if an icon is wanted (`<svg viewBox="0 0 16 16"><rect x="2.5" y="3.5" width="11" height="10" rx="2"/><path d="M2.5 6.5h11M5.5 2v3M10.5 2v3"/></svg>`, 12 px).

- [ ] **Step 8: Verify** — `npm --prefix frontend test`, `npm --prefix frontend run build`, `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q`; screenshot `meetings.html`, `?state=no-summary`, `?state=today`, `?state=delete` in dark and light. No coral fill on the selected row; Copy/Export/⋯ at the top; no card border around the summary; Today shows "9:00–9:12 AM".

- [ ] **Step 9: Commit**

```bash
git add frontend
git commit -m "Meetings: neutral selection, solid content, toolbar actions, short time ranges"
```

---

### Task 6: Transcript as conversation

**Files:**
- Create: `frontend/src/meetings/transcriptTurns.ts`, `frontend/tests/transcriptTurns.test.ts`
- Modify: `frontend/src/meetings/MeetingDetail.tsx:667-725` (the `.lines` render), `MeetingDetail.module.css:453-517`

**Interfaces:**
- Consumes: `TranscriptLine` (`time`, `start` seconds, `speakerNumber`, `speakerLabel`, `segmentIndex`, `overlap`, `text`).
- Produces `formatElapsed(seconds: number): string` and `groupTurns(lines: TranscriptLine[]): Turn[]`, where `interface Turn { speakerNumber: number; speakerLabel: string; start: number; time: string; lines: TranscriptLine[] }`.

- [ ] **Step 1: Failing node test** — `frontend/tests/transcriptTurns.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { TranscriptLine } from '../src/mock/meetings.ts';
import { formatElapsed, groupTurns } from '../src/meetings/transcriptTurns.ts';

function line(i: number, speakerNumber: number, label: string, start: number): TranscriptLine {
  return { time: `9:00:${String(start).padStart(2, '0')} AM`, speakerNumber, speakerLabel: label,
    text: `t${i}`, segmentIndex: i, confidence: null, overlap: false, start };
}

test('elapsed time is m:ss under an hour and h:mm:ss after', () => {
  assert.equal(formatElapsed(3), '0:03');
  assert.equal(formatElapsed(62.9), '1:02');
  assert.equal(formatElapsed(3735), '1:02:15');
});

test('consecutive lines from one speaker form one turn', () => {
  const lines = [line(0, 1, 'Alex', 3), line(1, 1, 'Alex', 9), line(2, 2, 'Priya', 14), line(3, 1, 'Alex', 27)];
  const turns = groupTurns(lines);
  assert.deepEqual(turns.map((t) => [t.speakerLabel, t.start, t.lines.length]), [['Alex', 3, 2], ['Priya', 14, 1], ['Alex', 27, 1]]);
  assert.equal(turns[0].time, '9:00:03 AM');
});

test('every segment appears exactly once and in order', () => {
  const lines = [line(0, 1, 'A', 0), line(1, 2, 'B', 1), line(2, 2, 'B', 2), line(3, 1, 'A', 3), line(4, 1, 'A', 4)];
  const flat = groupTurns(lines).flatMap((t) => t.lines.map((l) => l.segmentIndex));
  assert.deepEqual(flat, [0, 1, 2, 3, 4]);
});

test('a renamed label with the same number still splits on label change', () => {
  const lines = [line(0, 1, 'Speaker 1', 0), line(1, 1, 'Jordan', 2)];
  assert.equal(groupTurns(lines).length, 2);
});

test('empty transcript gives no turns', () => {
  assert.deepEqual(groupTurns([]), []);
});
```

- [ ] **Step 2: Run** — `npm --prefix frontend test` → FAIL (module not found).

- [ ] **Step 3: Implement** `frontend/src/meetings/transcriptTurns.ts`:

```ts
import type { TranscriptLine } from '../mock/meetings.ts';

export interface Turn {
  speakerNumber: number;
  speakerLabel: string;
  start: number;
  time: string;
  lines: TranscriptLine[];
}

/** "0:03", "12:40", "1:02:15" — elapsed since the meeting started. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** Consecutive lines with the same speaker number and label become one turn. */
export function groupTurns(lines: TranscriptLine[]): Turn[] {
  const turns: Turn[] = [];
  for (const line of lines) {
    const last = turns[turns.length - 1];
    if (last && last.speakerNumber === line.speakerNumber && last.speakerLabel === line.speakerLabel) {
      last.lines.push(line);
    } else {
      turns.push({ speakerNumber: line.speakerNumber, speakerLabel: line.speakerLabel,
        start: line.start, time: line.time, lines: [line] });
    }
  }
  return turns;
}
```

`mock/meetings.ts` must stay importable by Node: it has no non-type imports today (check the top of the file); if it gains any, move `TranscriptLine` into a `types.ts` with no imports. Note `import type … from '../mock/meetings.ts'` — the `.ts` extension is allowed by `allowImportingTsExtensions` in `tsconfig.json`.

Run `npm --prefix frontend test` → PASS.

- [ ] **Step 4: Render turns.** In `MeetingDetail.tsx`, replace the `detail.lines.map(...)` body with a map over `groupTurns(detail.lines)`:

```tsx
{groupTurns(detail.lines).map((turn) => (
  <div key={turn.lines[0].segmentIndex} className={styles.turn}>
    <span className={styles.elapsed} title={`${detail.approximate ? '≈' : ''}${turn.time}`}>
      {detail.approximate ? '≈' : ''}{formatElapsed(turn.start)}
    </span>
    <div className={styles.turnBody}>
      <button
        ref={(el) => {
          if (el) speakerRefs.current.set(turn.lines[0].segmentIndex, el);
          else speakerRefs.current.delete(turn.lines[0].segmentIndex);
        }}
        className={styles.speaker}
        style={{ color: speakerColor(turn.speakerNumber, colorCodeSpeakers) }}
        onClick={(e) => {
          const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
          onSpeakerClick(turn.lines[0], { top: rect.bottom, left: rect.left });
        }}
      >
        {turn.speakerLabel}
      </button>
      <p className={styles.turnText}>
        {turn.lines.map((line) => (
          <span
            key={line.segmentIndex}
            ref={(el) => {
              if (el) lineRefs.current.set(line.segmentIndex, el);
              else lineRefs.current.delete(line.segmentIndex);
            }}
            className={highlightSegment === line.segmentIndex ? `${styles.seg} ${styles.lineHighlight}` : styles.seg}
          >
            {line.overlap ? '[overlap] ' : ''}
            {/* keep the existing parts/mark rendering for this segment unchanged */}
            {' '}
          </span>
        ))}
      </p>
    </div>
  </div>
))}
```

Move the existing `parts ? parts.map(…<mark>…) : line.text` expression, unchanged, into the `<span>` where the comment is (it references `lineParts.get(line.segmentIndex)`, `currentMatch`, `styles.mark`, `styles.markCurrent` exactly as now). `lineRefs` keeps one element per segment, so find/jump scroll to the segment span; `speakerRefs` is keyed by the turn's first segment — grep `speakerRefs.current.get(` and make any lookup by a non-first segment fall back to the turn's first segment (build a `Map<segmentIndex, firstSegmentIndex>` from the turns with `useMemo`). The `autoOpenPopover` mock path uses `speakerRefs`; check `?state=popover` still opens.

- [ ] **Step 5: Styles** (replace `.lines`, `.line`, `.lineHead`, `.time`, `.lineText` in `MeetingDetail.module.css`):

```css
.lines { display: flex; flex-direction: column; gap: 16px; padding: 4px 0 8px; font: 400 14.5px/1.6 var(--sans); }
.turn { display: grid; grid-template-columns: 52px minmax(0, 1fr); column-gap: 12px; }
.elapsed { color: var(--text-lo); font: 400 11.5px/1.9 var(--sans); font-variant-numeric: tabular-nums; text-align: right; }
.turnBody { min-width: 0; }
.speaker { border: 0; background: transparent; padding: 0; font: 600 12.5px var(--sans); cursor: pointer; }
.turnText { margin: 2px 0 0; color: var(--text); max-width: 68ch; }
.seg { border-radius: 3px; }
.lineHighlight { background: color-mix(in srgb, var(--accent) 22%, transparent); }
```

Keep `.mark`, `.markCurrent`, `.findBar` rules (switch any `--coral-1` to `--accent`).

- [ ] **Step 6: Verify** — `npm --prefix frontend test`; `npm --prefix frontend run build`; token tests.

- [ ] **Step 7: Look at it** — mock: Transcript tab on `meetings.html`, then `?state=popover` (popover opens under a name), then `?state=search`, choose a result, confirm the transcript scrolls to and highlights that segment; open find (⌘F in the transcript), type "sync", step with Enter, confirm the current mark moves. Dark and light.

- [ ] **Step 8: Commit**

```bash
git add frontend
git commit -m "Transcript: speaker turns, proportional text, elapsed time"
```

---

### Task 7: Two panes, ⌘K search in the sidebar, Today as home

**Files:**
- Modify: `frontend/src/meetings/App.tsx:638-690` (layout), `App.module.css`
- Modify: `frontend/src/meetings/Sidebar.tsx`, `Sidebar.module.css`
- Modify: `frontend/src/meetings/MeetingList.tsx`, `MeetingList.module.css` (row layout)
- Modify: `frontend/src/meetings/MeetingDetail.tsx` (remove the search input from its toolbar; keep `searchFocusToken` handling by moving it to the sidebar input)

**Interfaces:**
- `Sidebar` new props: `searchValue: string`, `onSearchChange(value: string)`, `searchFocusToken?: number`, `children: ReactNode` (the list or search results, rendered under the nav).
- `MeetingDetail` loses `searchValue`, `onSearchChange`, `searchFocusToken`.

- [ ] **Step 1: Sidebar structure.** Top to bottom: drag-safe top inset (34 px, the TitleBar row), a search field (`placeholder="Search"`, a `⌘K` hint at its right end, `aria-label="Search meetings"`), nav rows "Today" (only when `features.calendar`) and "All meetings" with counts, two disclosure rows "Tags" and "People" (collapsed by default; a chevron rotates; expanded state shows the existing tag/person rows indented; remembered in `localStorage` keys `sidebar.tags.open` / `sidebar.people.open`, wrapped in try/catch), then `children` filling the remaining height with its own scroll, then the existing footer (Connect Claude, gear). Width 280 px.

- [ ] **Step 2: ⌘K / ⌘F.** In `App.tsx` add a window `keydown` listener: `(e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k'` → `preventDefault()` and bump `searchFocusToken` (the Sidebar focuses and selects its input when the token changes). ⌘F inside the meeting list keeps calling `onRequestSearchFocus`; ⌘F inside the transcript keeps opening find-in-transcript (unchanged). Esc in the search field clears it and returns to the list (existing `clearSearch` behaviour, moved).

- [ ] **Step 3: Layout.** `App.tsx` renders `<Sidebar …>{list-or-results}</Sidebar>` then either `TodayView` or `MeetingDetail`. The `.listColumn` wrapper and its CSS go away; `LibraryBanner` renders inside the sidebar above the list. `SearchResults` renders in the sidebar in place of the list while searching.

- [ ] **Step 4: Rows.** `MeetingList` row: line 1 = title (ellipsis, `min-width: 0`) + time (`flex-shrink: 0`, tabular nums, `var(--text-lo)`, 12 px); line 2 = people subtitle (ellipsis). Duration moves out of the row (it is in the detail meta line). The summary dot stays at the right of line 2. Rows get `border-radius: 7px`, `margin: 0 8px`, no bottom hairline; day headers become 11 px semibold `var(--text-lo)` without a fill. A row whose meeting is recording shows a red dot and "Live" in `var(--rec)` instead of the time — only if `MeetingMeta` already exposes that state; if it does not, skip this (no bridge change in this plan) and note it in Execution notes.

- [ ] **Step 5: Today as home.** Initial `today` state: `true` when `filters.features.calendar && todayConnection === 'connected'` at first load (embedded); mock keeps `TODAY_STATES.includes(mockState)`. Implement as a one-shot effect that runs when the first calendar payload arrives and only if the user has not selected anything yet (track with a ref `userNavigated`).

- [ ] **Step 6: Verify** — build, node tests, token tests. Mock: `meetings.html` (two panes, sidebar list, long "Design Review — Onboarding Flow" truncates while its time stays whole), `?state=search` (results in the sidebar; Esc returns), `?state=today`, `?state=empty`, ⌘K focuses search, ArrowUp/Down moves selection, ⌘⌫ opens Delete. Dark and light. Resize the preview to 900 × 600: no horizontal scrollbar; detail column shrinks.

- [ ] **Step 7: Commit**

```bash
git add frontend
git commit -m "Meetings: two panes, sidebar search with ⌘K, Today as home"
```

---

### Task 8: Record banner — ⋯ menu, Undo notice, dynamic colours

**Files:**
- Modify: `speakeasy/record_prompt.py` (`BannerText.tone`, `RecordPromptCoordinator.calendar_disabled`)
- Modify: `speakeasy/ui/record_prompt_panel.py`
- Modify: `speakeasy/ui/record_prompt_controller.py`
- Test: `tests/test_record_prompt.py`, `tests/test_record_prompt_panel.py`, `tests/test_record_prompt_controller.py`

**Interfaces:**
- `BannerText` gains `tone: str = "record"` (`"record"` or `"stop"`) and `kind: str = "calendar"` (`"calendar"` or `"call"`; call-ended is `"call"`). Field order: `title, subtitle, primary, secondary, more=(), tone="record", kind="calendar"`.
- `RecordPromptCoordinator.calendar_disabled() -> None`: drops a calendar `Offer` without marking events prompted.
- Panel: `show(title, subtitle, primary, secondary, more, tone="record", off_title="")` — `off_title` is the ⋯ menu item text; empty hides ⋯. New `show_notice(title, subtitle, action)` shows title, subtitle and one button (`action`, e.g. "Undo") for `NOTICE_SECONDS = 5.0`, then hides. Clicks: `moreOffClicked:` → `target.bannerTurnOff_(sender)`; notice button → `target.bannerUndo_(sender)`.
- Controller: `bannerTurnOff_(sender)`, `bannerUndo_(sender)`.

- [ ] **Step 1: Failing coordinator/text tests** (append to `tests/test_record_prompt.py`, using that file's existing event helper and `NOW`):

```python
def test_banner_text_tone_and_kind():
    from speakeasy.record_prompt import CallEnded, Offer, banner_text
    ended = banner_text(CallEnded(), NOW)
    assert (ended.tone, ended.kind, ended.primary) == ("stop", "call", "Stop")
    call = banner_text(Offer("call", (), NOW), NOW)
    assert (call.tone, call.kind) == ("record", "call")
    cal = banner_text(Offer("calendar", (ev("k1"),), NOW), NOW)
    assert (cal.tone, cal.kind) == ("record", "calendar")


def test_calendar_disabled_drops_only_calendar_offers_and_marks_nothing():
    from speakeasy.record_prompt import Offer, RecordPromptCoordinator
    c = RecordPromptCoordinator(set())
    c.banner = Offer("calendar", (ev("k1"),), NOW)
    c.calendar_disabled()
    assert c.banner is None and c.prompted == set()
    c.banner = Offer("call", (), NOW)
    c.calendar_disabled()
    assert c.banner is not None
```

(Use the file's existing names for the event factory and clock; if they differ from `ev`/`NOW`, adapt the two tests to them.)

- [ ] **Step 2: Failing controller tests** (append to `tests/test_record_prompt_controller.py`; extend `FakePanel` first):

```python
# in FakePanel:
    def show(self, title, subtitle, primary, secondary, more, tone="record", off_title=""):
        self.subtitles.append(subtitle)
        self.calls.append(("show", title, primary, secondary, tuple(more)))
        self.offs.append(off_title)
        self.tones.append(tone)

    def show_notice(self, title, subtitle, action):
        self.calls.append(("notice", title, subtitle, action))
# and in __init__: self.offs = []; self.tones = []


def test_turn_off_calendar_prompts_from_the_banner_and_undo(monkeypatch):
    c, panel, _, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    assert panel.offs[-1] == "Turn off calendar prompts"
    c.bannerTurnOff_(None)
    assert settings.get_meeting_settings()["offer_to_record"] is False
    assert panel.calls[-1] == ("notice", "Calendar prompts are off",
                               "Turn back on in Meetings › Settings", "Undo")
    assert c.engine.begun == [] and c.engine.ended == 0
    c.bannerUndo_(None)
    assert settings.get_meeting_settings()["offer_to_record"] is True
    assert panel.calls[-1] == ("hide",)
    c.shutdown()


def test_turning_off_call_prompts_during_recording_never_ends_it(monkeypatch):
    engine = FakeEngine(state="meeting_recording")
    c, panel, _, times = make(monkeypatch, engine)
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=15)
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=20)
    c.callObserved_(False)
    times[0] = NOW + timedelta(seconds=90)
    c.callObserved_(False)
    assert panel.calls[-1][:2] == ("show", "Call ended") and panel.tones[-1] == "stop"
    assert panel.offs[-1] == "Turn off call prompts"
    c.bannerTurnOff_(None)
    assert settings.get_meeting_settings()["detect_calls"] is False
    assert panel.calls[-1][1] == "Call prompts are off"
    assert engine.ended == 0 and engine.begun == []
    assert c.probe_wanted is False
    c.shutdown()


def test_undo_after_the_notice_is_gone_does_nothing(monkeypatch):
    c, panel, _, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    c.bannerTurnOff_(None)
    c.noticeExpired()
    c.bannerUndo_(None)
    assert settings.get_meeting_settings()["offer_to_record"] is False
    c.shutdown()
```

(Adjust the call-ended timing to the coordinator's `CALL_START`/`CALL_END` constants if they differ from 10 s / 60 s; read them from `speakeasy/record_prompt.py`.)

- [ ] **Step 3: Failing panel test** (append to `tests/test_record_prompt_panel.py`; extend its `Target` with `bannerTurnOff_` and `bannerUndo_` that append `"off"` / `"undo"`):

```python
def test_more_button_notice_and_title_never_overlap():
    from speakeasy.ui import record_prompt_panel as rpp
    assert rpp.NOTICE_SECONDS == 5.0
    target = Target()
    p = RecordPromptPanel.alloc().initWithTarget_(target)
    p.show("A very long meeting title that will not fit in the banner at all, really",
           "Starting now · 3 invited", "Record", "Not Now", [], off_title="Turn off calendar prompts")
    assert not p._off.isHidden()
    assert p._title.frame().origin.x + p._title.frame().size.width <= p._off.frame().origin.x - 4
    p.offChosen_(None)
    p.show_notice("Calendar prompts are off", "Turn back on in Meetings › Settings", "Undo")
    assert p._secondary.title() == "Undo" and not p._secondary.isHidden()
    assert p._primary.isHidden() and p._off.isHidden() and p._more.isHidden()
    p.secondaryClicked_(None)
    assert target.calls[-2:] == ["off", "undo"]
    p.show("Call ended", "Stop recording?", "Stop", "Keep Recording", [], tone="stop", off_title="")
    assert p._off.isHidden()
    p.hide()


def test_every_banner_button_title_fits_its_button():
    p = RecordPromptPanel.alloc().initWithTarget_(Target())
    for primary, secondary in (("Record", "Not Now"), ("Stop", "Keep Recording")):
        p.show("T", "S", primary, secondary, ["One", "Two"], off_title="Turn off call prompts")
        for button in (p._primary, p._secondary, p._more):
            assert button.fittingSize().width <= button.frame().size.width + 0.5, button.title()
    p.show_notice("Call prompts are off", "Turn back on in Meetings › Settings", "Undo")
    assert p._secondary.fittingSize().width <= p._secondary.frame().size.width + 0.5
    p.hide()
```

- [ ] **Step 4: Run all three to see them fail**

Run: `.venv/bin/python -m pytest tests/test_record_prompt.py tests/test_record_prompt_panel.py tests/test_record_prompt_controller.py -q`
Expected: FAIL (`unexpected keyword argument 'off_title'`, missing `calendar_disabled`, missing `bannerTurnOff_`).

- [ ] **Step 5: Coordinator and text** (`speakeasy/record_prompt.py`):

```python
@dataclass(frozen=True)
class BannerText:
    title: str
    subtitle: str
    primary: str
    secondary: str
    more: tuple[str, ...] = ()
    tone: str = "record"        # "record" (coral) or "stop" (recording red)
    kind: str = "calendar"      # which setting the ⋯ menu turns off


def banner_text(banner, now: datetime) -> BannerText:
    if isinstance(banner, CallEnded):
        return BannerText("Call ended", "Stop recording?", "Stop", "Keep Recording",
                          tone="stop", kind="call")
    if banner.kind == "call":
        if banner.events:
            return BannerText(banner.events[0].title, "Call in progress", "Record", "Not Now",
                              kind="call")
        return BannerText("Record this call?", "Another app is using the microphone",
                          "Record", "Not Now", kind="call")
    first = banner.events[0]
    return BannerText(first.title, f"{_when(first, now)} · {len(first.people)} invited",
                      "Record", "Not Now", tuple(e.title for e in banner.events[1:]))
```

and in `RecordPromptCoordinator`, beside `calls_disabled`:

```python
    def calendar_disabled(self) -> None:
        """Calendar offers were turned off: drop a calendar banner (nothing is marked)."""
        if isinstance(self.banner, Offer) and self.banner.kind == "calendar":
            self.banner = None
```

- [ ] **Step 6: Panel** (`speakeasy/ui/record_prompt_panel.py`):
  - Constants: `NOTICE_SECONDS = 5.0`; `OFF_SIZE = 22.0`.
  - Dynamic colours (module level):

```python
def _dynamic(light_rgb, dark_rgb):
    from AppKit import NSAppearanceNameAqua, NSAppearanceNameDarkAqua

    def pick(appearance):
        dark = appearance.bestMatchFromAppearancesWithNames_(
            [NSAppearanceNameAqua, NSAppearanceNameDarkAqua]) == NSAppearanceNameDarkAqua
        r, g, b = dark_rgb if dark else light_rgb
        return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, 1.0)
    return NSColor.colorWithName_dynamicProvider_(None, pick)


CORAL = _dynamic((0xB8, 0x55, 0x1F), (0xE8, 0x95, 0x5A))
STOP_RED = _dynamic((0xD3, 0x3A, 0x2F), (0xFF, 0x5A, 0x4E))
ON_ACCENT = _dynamic((0xFF, 0xFF, 0xFF), (0x1D, 0x1F, 0x1E))
```

  - Replace the hard-coded `setBezelColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.45, 0.38, 1.0))` with `self._primary.setBezelColor_(CORAL)`.
  - Add the ⋯ button top-right: `self._off = self._button(b"offClicked:", NSMakeRect(WIDTH - 12 - OFF_SIZE, HEIGHT - 12 - OFF_SIZE, OFF_SIZE, OFF_SIZE))`, `setBordered_(False)`, title `"⋯"`, tooltip `"Prompt options"`, hidden by default. Shrink `_title` to `NSMakeRect(16, 58, WIDTH - 32 - OFF_SIZE - 6, 18)`.
  - A helper sets the primary label colour:

```python
    @objc.python_method
    def _set_primary(self, title, tone):
        from AppKit import NSAttributedString, NSFont, NSFontAttributeName, NSForegroundColorAttributeName
        self._primary.setBezelColor_(STOP_RED if tone == "stop" else CORAL)
        self._primary.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            title, {NSForegroundColorAttributeName: ON_ACCENT,
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(13.0, NSFontWeightSemibold)}))
```

    (`self._primary.title()` still returns the plain string for the existing tests; keep `setTitle_(primary)` before `_set_primary`.)
  - `show(self, title, subtitle, primary, secondary, more, tone="record", off_title="")`: as now, plus `self._set_primary(primary, tone)`, `self._off_title = off_title`, `self._off.setHidden_(not off_title)`, `self._mode = "offer"`.
  - `offClicked_(self, sender)`: pop up an `NSMenu` with one item `self._off_title` (action `offChosen:`, target self) positioned under the button, as `moreClicked_` does.
  - `offChosen_(self, item)`: `self.target.bannerTurnOff_(item)`.
  - `show_notice(self, title, subtitle, action)`: cancel the hide timer; set title/subtitle; hide `_primary`, `_more`, `_off`; show `_secondary` titled `action`; `self._mode = "notice"`; ensure the panel is visible (same fade-in as `show`); schedule `noticeDone:` after `NOTICE_SECONDS`.
  - `secondaryClicked_`: if `self._mode == "notice"` → `self.target.bannerUndo_(sender)`; else the existing `bannerSecondary_`.
  - `noticeDone_(self, timer)`: `self._hide_timer = None; self.hide(); self.target.noticeExpired()` (guard with `hasattr(self.target, "noticeExpired")` so the test `Target` need not define it).
  - `show_confirmation` also hides `_off`.

- [ ] **Step 7: Controller** (`speakeasy/ui/record_prompt_controller.py`):

```python
_OFF_TITLES = {"calendar": "Turn off calendar prompts", "call": "Turn off call prompts"}
_OFF_NOTICES = {"calendar": "Calendar prompts are off", "call": "Call prompts are off"}
NOTICE_HINT = "Turn back on in Meetings › Settings"
```

  - In `initWithEngine_`: `self._undo_kind = None`.
  - `_render`: pass `tone=text.tone, off_title=_OFF_TITLES[text.kind]` to `self.panel.show(...)`. If `self._undo_kind` is set, a notice is showing: return before drawing a new banner unless the coordinator has a banner (a new banner replaces the notice and clears `_undo_kind`).
  - New handlers:

```python
    def bannerTurnOff_(self, sender):
        banner = self.coordinator.banner
        if banner is None:
            return
        kind = banner_text(banner, _now()).kind
        if kind == "calendar":
            settings.set_meeting_settings(offer_to_record=False)
            self.coordinator.calendar_disabled()
        else:
            settings.set_meeting_settings(detect_calls=False)
            self.coordinator.calls_disabled()
        self._update_probe(self.engine.state.value, settings.get_meeting_settings())
        self._shown = None
        self._shown_text = None
        self._undo_kind = kind
        self.panel.show_notice(_OFF_NOTICES[kind], NOTICE_HINT, "Undo")

    def bannerUndo_(self, sender):
        kind, self._undo_kind = self._undo_kind, None
        if kind == "calendar":
            settings.set_meeting_settings(offer_to_record=True)
        elif kind == "call":
            settings.set_meeting_settings(detect_calls=True)
        else:
            return
        self._update_probe(self.engine.state.value, settings.get_meeting_settings())
        self.panel.hide()

    @objc.python_method
    def noticeExpired(self):
        self._undo_kind = None
```

  `bannerTurnOff_` never touches the engine. The call path keeps `take_call_waved_off` semantics unchanged (turning off is not a wave-off).

- [ ] **Step 8: Run the banner suites**

Run: `.venv/bin/python -m pytest tests/test_record_prompt.py tests/test_record_prompt_panel.py tests/test_record_prompt_controller.py tests/test_call_detect.py -q` → PASS, including the existing geometry/timing and never-takes-focus tests.

- [ ] **Step 9: Commit**

```bash
git add speakeasy/record_prompt.py speakeasy/ui/record_prompt_panel.py speakeasy/ui/record_prompt_controller.py tests/test_record_prompt*.py
git commit -m "Record banner: turn prompts off from the banner, Undo, light/dark colours"
```

---

### Task 9: Every window resizes; layouts adapt

**Files:**
- Create: `speakeasy/ui/window_sizes.py`, `tests/test_window_sizes.py`, `frontend/scripts/overflow-check.js`
- Modify: `speakeasy/ui/main_window.py:50`, `speakeasy/ui/training_window.py:19,86`, `speakeasy/ui/diagnostic_window.py:32` (construct resizable windows with a min size and autosave name)
- Modify: `frontend/src/components/GlassPanel.tsx` (`?fit` preview mode)
- Modify (fixed pixel widths → fluid): `meetings/Sidebar.module.css`, `meetings/MeetingDetail.module.css`, `meetings/TodayView.module.css`, `meetings/Sheet.module.css`, `meetings/SettingsSheet.module.css`, `meetings/ConnectClaudeSheet.module.css`, `meetings/ConfirmSheet.module.css`, `meetings/SearchResults.module.css`, `meetings/MeetingList.module.css`, `dock/App.module.css`, `training/App.module.css`, `diagnostic/App.module.css`

**Interfaces:**
- Produces `window_sizes.SIZES: dict[str, WindowSize]` with `WindowSize(default: tuple[float, float], minimum: tuple[float, float], autosave: str)` for `"dock"`, `"training"`, `"diagnostic"`. Meetings keeps its own `_MIN_SIZE`/autosave in `meetings_window.py` (unchanged).
- `WebWindow(..., resizable=True, min_size=...)` already exists; callers pass `resizable=True, min_size=size.minimum` and then `self._web.window.setFrameAutosaveName_(size.autosave)`, as `meetings_window.py:62-67` does.

- [ ] **Step 1: Failing test** — `tests/test_window_sizes.py`:

```python
from pathlib import Path

from speakeasy.ui.window_sizes import SIZES

UI = Path(__file__).resolve().parents[1] / "speakeasy" / "ui"


def test_pinned_defaults_minimums_and_autosave_names():
    assert {k: (v.default, v.minimum, v.autosave) for k, v in SIZES.items()} == {
        "dock": ((360, 430), (340, 400), "SpeakeasyDockFrame"),
        "training": ((640, 440), (560, 400), "SpeakeasyTrainingFrame"),
        "diagnostic": ((660, 600), (560, 520), "SpeakeasyDiagnosticFrame"),
    }
    for v in SIZES.values():
        assert v.minimum[0] <= v.default[0] and v.minimum[1] <= v.default[1]


def test_every_web_window_is_resizable_and_remembers_its_frame():
    for module, key in (("main_window.py", "dock"), ("training_window.py", "training"),
                        ("diagnostic_window.py", "diagnostic"), ("meetings_window.py", None)):
        text = (UI / module).read_text()
        assert "resizable=True" in text, module
        assert "setFrameAutosaveName_" in text, module
        if key:
            assert f'SIZES["{key}"]' in text, module
```

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_window_sizes.py -q` → FAIL (`ModuleNotFoundError: speakeasy.ui.window_sizes`).

- [ ] **Step 3: Implement** — `speakeasy/ui/window_sizes.py`:

```python
"""Default size, minimum size and frame-autosave name of each small web
window. The minimum is the smallest size at which the page does not clip
(checked with frontend/scripts/overflow-check.js). Meetings sizes itself
from the screen in meetings_window.py."""

from typing import NamedTuple


class WindowSize(NamedTuple):
    default: tuple[float, float]
    minimum: tuple[float, float]
    autosave: str


SIZES = {
    "dock": WindowSize((360, 430), (340, 400), "SpeakeasyDockFrame"),
    "training": WindowSize((640, 440), (560, 400), "SpeakeasyTrainingFrame"),
    "diagnostic": WindowSize((660, 600), (560, 520), "SpeakeasyDiagnosticFrame"),
}
```

In each window module, for example `main_window.py`:

```python
        size = SIZES["dock"]
        self._web = WebWindow("Speakeasy", *size.default, "dock", dispatcher,
                              resizable=True, min_size=size.minimum)
        # Overrides the centred default frame when a saved one exists.
        self._web.window.setFrameAutosaveName_(size.autosave)
```

with `from .window_sizes import SIZES`. Same for Training (`SIZES["training"]`, delete `_W, _H`; grep the module for other uses of `_W`/`_H` and switch them to `size.default`) and Microphone Check (`SIZES["diagnostic"]`). Run the test → PASS.

- [ ] **Step 4: Preview at any size.** `GlassPanel.tsx`: in the non-embedded branch, if `new URLSearchParams(window.location.search).has('fit')`, use `{ width: '100vw', height: '100vh' }` (like embedded) so the browser preview can be resized like the real window. Mock state and `fit` combine: `meetings.html?state=today&fit`.

- [ ] **Step 5: Overflow checker** — `frontend/scripts/overflow-check.js` (run in the preview page with the browser tool's JavaScript runner; it returns a list, empty when nothing clips):

```js
// Lists elements whose content is cut off: horizontal page scroll, text or
// buttons wider than their box without an ellipsis, and boxes poking out of
// the window. Paste into the page console or run via the browser tool.
(() => {
  const out = [];
  const W = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > W + 1) out.push(`page scrolls sideways: ${document.documentElement.scrollWidth} > ${W}`);
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const clipsX = el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'visible'
      && cs.overflowX !== 'auto' && cs.overflowX !== 'scroll' && cs.textOverflow !== 'ellipsis';
    const isControl = el.matches('button, [role="button"], [role="tab"], [role="radio"], input, label');
    if (clipsX && (isControl || el.children.length === 0)) out.push(`clipped: ${el.tagName.toLowerCase()}.${el.getAttribute('class')} "${(el.textContent || '').trim().slice(0, 40)}"`);
    if (isControl && el.scrollWidth > Math.ceil(r.width) + 1) out.push(`control narrower than its text: "${(el.textContent || '').trim().slice(0, 40)}"`);
    if (r.right > W + 1 && cs.position !== 'fixed') out.push(`outside the window: ${el.tagName.toLowerCase()}.${el.getAttribute('class')}`);
  }
  return [...new Set(out)];
})();
```

- [ ] **Step 6: Make layouts fluid.** Apply exactly:

  - Meetings sidebar (`Sidebar.module.css .sidebar`, and the widths of `SearchResults`/`MeetingList` containers that Task 7 moved into it): `width: clamp(220px, 24vw, 300px); flex-shrink: 0;` — delete the fixed `280px`/`200px` widths; children use `width: 100%`.
  - Detail column (`MeetingDetail.module.css .bodyInner` from Task 5): `max-width: 640px;` plus
    ```css
    @media (min-width: 1400px) { .bodyInner { max-width: 720px; } }
    ```
    The toolbar search width rule (`width: 220px`, line ~23) is removed with the search move in Task 7; the event menu (`min-width: 240px; max-width: 340px`) becomes `min-width: min(240px, 80vw); max-width: min(340px, 90vw)`; `max-width: 320px` (line ~380) becomes `max-width: min(320px, 100%)`.
  - Today (`TodayView.module.css`): the outer content gets `width: 100%; max-width: 760px; margin: 0 auto;`; the fixed time column `width: 116px` becomes `width: 9.5em; flex-shrink: 0`.
  - Sheets: `Sheet.module.css` panel gets `max-width: calc(100vw - 48px); max-height: calc(100vh - 48px); overflow-y: auto;`; `SettingsSheet` `width: 380px` → `width: min(380px, calc(100vw - 48px))`; `ConnectClaudeSheet` `width: 560px` → `width: min(560px, calc(100vw - 48px))`; `ConfirmSheet` `width: 320px` → `width: min(320px, calc(100vw - 48px))`; the two `width: 104px` buttons → `min-width: 104px` (they may grow to fit their label).
  - `SpeakerPopover` `width: 260px` → `width: min(260px, calc(100vw - 24px))`.
  - Dock (`dock/App.module.css`): the action-button grid becomes `display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px;`; the capture select (`min-width: 190px; max-width: 240px`) becomes `min-width: 0; flex: 1 1 190px; max-width: none`; the menu at line ~232 `width: 230px` → `width: min(230px, calc(100vw - 24px))`; the waveform stretches (`width: 100%`).
  - Training (`training/App.module.css`): sessions column `width: 222px` → `width: clamp(180px, 30%, 260px); flex-shrink: 0`; `max-width: 340px`/`360px` text blocks keep their measure but add `min-width: 0`.
  - Microphone Check (`diagnostic/App.module.css`): content `width: 100%`; `.prompt` keeps `max-height` but uses `max-height: min(255px, 40vh)`.
  - Every flex/grid child that holds text gets `min-width: 0` so long text ellipsizes instead of pushing the layout wider.

- [ ] **Step 7: Check every page at four sizes, both appearances.** Dev server on 5199. For each page and each size, set the browser pane with `resize_window` (`colorScheme` dark, then light), load the page with `&fit` (or `?fit`), run `overflow-check.js`, and screenshot once per page per appearance at the minimum size:

  | Page (preview URL) | Sizes |
  |---|---|
  | `meetings.html?fit`, `?state=today&fit`, `?state=settings&fit`, `?state=search&fit`, Transcript tab | 820×520, 1100×700, 1440×900, 2560×1440 |
  | `dock.html?fit`, `dock.html?state=recording-linked&fit` | 340×400, 360×430, 520×600, 800×900 |
  | `training.html?fit` | 560×400, 640×440, 1000×700, 1600×1000 |
  | `diagnostic.html?fit` | 560×520, 660×600, 1000×800, 1600×1000 |

  Expected: `overflow-check.js` returns `[]` everywhere. (Trial on 2 Oct 2026 against today's mock: `[]` at 1100×720, where the list's ellipsis truncation is correctly not flagged; at 820×520 it flags the fixed 1040 px preview panel as outside the window, which `?fit` resolves. It would also have caught the review page's clipped Stop button as "control narrower than its text".) If a page clips at its minimum, fix the CSS; raise the minimum in `window_sizes.py` (and its test) only when the content genuinely cannot fit, and write why in Execution notes. Record the table of results in Execution notes.

- [ ] **Step 8: Run tests and build** — `.venv/bin/python -m pytest tests/test_window_sizes.py tests/test_meetings_window_size.py tests/test_frontend_tokens.py -q`; `npm --prefix frontend test`; `npm --prefix frontend run build`.

- [ ] **Step 9: Commit**

```bash
git add speakeasy/ui/window_sizes.py speakeasy/ui/main_window.py speakeasy/ui/training_window.py speakeasy/ui/diagnostic_window.py tests/test_window_sizes.py frontend
git commit -m "Windows: all resizable with remembered frames; layouts adapt without clipping"
```

---

### Task 10: Docs, full suite, launch and look

**Files:**
- Modify: `README.md` (Record prompts section: the ⋯ menu and Undo; a new "Appearance" line under Meetings › Settings; every window resizes and remembers its size), `AGENTS.md` (appearance applied app-level in `speakeasy/ui/appearance.py`; tokens-only CSS rule and `node --test`; `npm --prefix frontend test` added to the Build/run/test block)
- Modify: this plan's Execution notes

- [ ] **Step 1: Docs** — add the lines above; in AGENTS.md's Build / run / test block add `npm --prefix frontend test   # frontend pure-helper tests (node --test, no deps)`.

- [ ] **Step 2: Full suite** — `.venv/bin/python -m pytest -q` with Bash `timeout: 600000`; `npm --prefix frontend test`; `npm --prefix frontend run build`. All pass; record counts in Execution notes.

- [ ] **Step 3: Mock look, both modes** — dev server on 5199; screenshots of `meetings.html`, Transcript tab, `?state=today`, `?state=settings`, `?state=search`, `dock.html`, `training.html`, `diagnostic.html` with `colorScheme` dark and light. Keep the screenshots in the scratchpad, not the repo.

- [ ] **Step 4: Real app launch (temp HOME)** — `scripts/build_app.sh` (not `--install`; installing stops Claude Desktop's Speakeasy MCP connector — memory: install-kills-mcp-connector). Launch `dist/Speakeasy.app/Contents/MacOS/Speakeasy` through Python `subprocess.Popen(..., env={**os.environ, "HOME": tmp})` with a temp HOME holding a few copied JSON meetings (memory: installed-app-ui-checks). Open Meetings; switch Appearance Light → Dark → System in Settings and confirm the Meetings window and Dock repaint without reopening. Drag-resize Meetings, the Dock, Training and Microphone Check to their minimums and to large sizes; nothing clips. Quit and relaunch: each window reopens at the size it was left. Screen control may be declined; if so, hand this check to the user with exact steps and record that it was not done by the agent.

- [ ] **Step 5: Banner check (user, live)** — the banner needs a real calendar event or call; ask the user to: open a call app (synthetic call is fine), wait for "Record this call?", click ⋯ → "Turn off call prompts", see "Call prompts are off" with Undo, click Undo, confirm Settings shows the switch on again. Also look at the banner in light and dark. Record the result in Execution notes. This also re-runs record-prompts open check 4's surface; checks 2, 4 and 6 from that plan remain the user's.

- [ ] **Step 6: Commit and report** — `git add README.md AGENTS.md docs/superpowers/plans/2026-10-02-quiet-library.md && git commit -m "Quiet library: docs and acceptance notes"`. Report which checks were run by the agent and which are the user's.

---

## Follow-up plan (not in this plan)

Recording pill with a capture drawer; menu-bar popover replacing the Dock (touches `menubar.py` — coordinate with the Codex `latency-cancellation` worktree first); meeting notepad; "Summary written by Claude · edited by you" provenance (needs `updated_by` in the `meetings.get` payload); a "Live" state on sidebar rows if Task 7 Step 4 had to skip it.

## Execution notes

(Empty. Record decisions, deviations, test counts and live-check results here as tasks complete.)

### Progress (2 Oct 2026, paused at usage limit)
- Tasks 1–7 complete and reviewed (implementers Sonnet, reviewers Opus, mutation-checked), branch `quiet-library`, worktree `.claude/worktrees/quiet-library`, head cb775b6. Next: Task 8 (brief already extracted).
- Full ledger with every ruling and deferred minor: `.superpowers/sdd/2026-10-02-quiet-library/progress.md` (git-ignored, in the worktree).
- Rulings: highlights/search marks use `--amber`, not coral; non-Record coral removed (popover, Connect Claude, library banner); day headers flat and non-sticky; reviewers may mutate temporarily.
- For the final fix wave: Switch.module.css on-state is still coral; no test for `apply_saved()` at launch; token lint doesn't check that `var(--x)` names exist; add a test for two speaker numbers sharing a label; `--surface-sticky` is unused; ⌘K fires while a sheet is open.
- "Live" sidebar row skipped: MeetingMeta has no recording state.
