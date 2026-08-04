# Native Embedding (Phase 2) Implementation Plan

> **Historical implementation record — completed.** The WKWebView host,
> bridge, three web-rendered windows, packaging, and tests described here are
> implemented. The live bridge now also includes speaker relabeling and
> privacy-safe global/selected-app capture state not present in this original
> plan. Preserve the checkboxes and code below as the planning-time snapshot;
> use `README.md`, `AGENTS.md`, and the current source for operational truth.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three native PyObjC windows (dock/main, meetings, training) with WKWebView-hosted pages from `frontend/`, wired to the real engine over a JS↔Python bridge, packaged fully offline.

**Architecture:** A reusable `WebWindow` (glass.py NSWindow + NSVisualEffectView + transparent WKWebView) hosts each built page from `frontend/dist/`. A single `WKScriptMessageHandler` carries JS→Python calls into a pure-Python dispatcher; Python→JS pushes hop to the main thread (existing `performSelectorOnMainThread` pattern) then `evaluateJavaScript`, queued until the page finishes loading. The three window controllers keep their existing class names and selector surfaces so `menubar.py` wiring is nearly untouched.

**Tech Stack:** PyObjC (+ new `pyobjc-framework-WebKit` pin), WKWebView, React/Vite/TS frontend from phase 1, pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-native-embed-design.md`

## Global Constraints

- **Fully offline at runtime.** Pages load via `loadFileURL_allowingReadAccessToURL_` from `frontend/dist/` (dev) or `Contents/Resources/frontend/` (bundled). No server, no network, ever.
- **Threading model (CLAUDE.md) is inviolable.** All WKWebView interaction (create, load, `evaluateJavaScript`) is main-thread only. Bridge handlers run on the main thread and must return fast; engine calls that do work internally submit to their own executors (`engine.begin_meeting`/`end_meeting` already do). Engine→UI pushes use `performSelectorOnMainThread_withObject_waitUntilDone_(selector, arg, False)` exactly as `menubar.py` does today.
- **Tests never touch real WebKit/AppKit/mic/model.** Pure bridge logic lives in `speakeasy/ui/webbridge.py` with **no AppKit/WebKit imports at module level**; `speakeasy/ui/webwindow.py` imports WebKit lazily (mirroring `diarizer.py`'s lazy `sherpa_onnx` import) so the module stays importable in tests.
- **ObjC class names are global** (see `glass.py` docstring): new NSObject subclasses must have unique, prefixed names (`SpeakeasyScriptHandler`, `SpeakeasyNavDelegate`) and be defined exactly once.
- Exact pins: add `pyobjc-framework-WebKit==12.2.1` to `requirements.txt` (matches the existing `pyobjc-*==12.2.1` pins). No other new dependencies.
- Frontend commands run in `frontend/` (`npm run build` = `tsc --noEmit && vite build`). Python tests: `.venv/bin/python -m pytest`. **When manually running the app from source, `frontend/dist` must exist** — run `npm --prefix frontend run build` first.
- Engine mode strings crossing the bridge are the raw `State.value` strings: `loading, ready, recording, transcribing, paused, meeting_recording, meeting_processing`.
- The bridge wire format: JS→Python `{id: number, method: string, params: object}`; Python→JS `speakeasyBridge._resolve(id, result)` / `speakeasyBridge._reject(id, "message")` / `speakeasyBridge._emit("event", payload)` via `evaluateJavaScript`.
- Existing controller public surfaces are preserved: `MainWindowController.alloc().initWithEngine_(engine)`, `.show()`, selectors `engineStateChanged_`, `meetingProgress_`, `meetingSaved_`; `MeetingsWindowController.alloc().init()`, `.show()`; `TrainingWindowController.alloc().initWithEngine_(engine)`, `.showForProfile_(profile)`. `menubar.py` changes are limited to Task 8's one addition.

---

### Task 1: Frontend bridge + embedded mode foundation

**Files:**
- Create: `frontend/src/bridge.ts`
- Modify: `frontend/vite.config.ts` (add `base: './'`)
- Modify: `frontend/src/styles/tokens.css` (embedded-mode globals)
- Modify: `frontend/src/components/GlassPanel.tsx` + `GlassPanel.module.css` (embedded fill variant)
- Modify: `frontend/src/components/TitleBar.module.css` (hide lights when embedded)
- Modify: `frontend/src/components/StatusDot.tsx` + `StatusDot.module.css` (add `amber` variant)
- Modify: `frontend/src/components/GlassButton.tsx` (add `onClick` prop)

**Interfaces:**
- Consumes: nothing new.
- Produces: `bridge` singleton from `src/bridge.ts` — `bridge.embedded: boolean`, `bridge.call<T>(method: string, params?: object): Promise<T>`, `bridge.on(event: string, handler: (payload: any) => void): () => void`. Global side effect: sets `window.speakeasyBridge` and adds class `embedded` to `<html>` when the native handler exists. `StatusDot` variant type becomes `'green' | 'rec' | 'amber'`. `GlassButton` gains `onClick?: () => void`.

- [ ] **Step 1: Create `frontend/src/bridge.ts`**

```ts
type Payload = Record<string, unknown>;

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
}

interface WebKitHandler {
  postMessage(msg: unknown): void;
}

declare global {
  interface Window {
    webkit?: { messageHandlers?: { speakeasy?: WebKitHandler } };
    speakeasyBridge?: SpeakeasyBridge;
  }
}

class SpeakeasyBridge {
  readonly embedded: boolean;
  private nextId = 1;
  private pending = new Map<number, PendingCall>();
  private listeners = new Map<string, Set<(payload: unknown) => void>>();

  constructor() {
    this.embedded = Boolean(window.webkit?.messageHandlers?.speakeasy);
  }

  call<T>(method: string, params: Payload = {}): Promise<T> {
    if (!this.embedded) {
      return Promise.reject(new Error(`bridge not available: ${method}`));
    }
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      window.webkit!.messageHandlers!.speakeasy!.postMessage({ id, method, params });
    });
  }

  on(event: string, handler: (payload: unknown) => void): () => void {
    let set = this.listeners.get(event);
    if (!set) {
      set = new Set();
      this.listeners.set(event, set);
    }
    set.add(handler);
    return () => set!.delete(handler);
  }

  /* Called from Python via evaluateJavaScript — do not rename. */
  _resolve(id: number, result: unknown): void {
    const p = this.pending.get(id);
    if (!p) return;
    this.pending.delete(id);
    p.resolve(result);
  }

  _reject(id: number, message: string): void {
    const p = this.pending.get(id);
    if (!p) return;
    this.pending.delete(id);
    p.reject(new Error(message));
  }

  _emit(event: string, payload: unknown): void {
    this.listeners.get(event)?.forEach((h) => h(payload));
  }
}

export const bridge = new SpeakeasyBridge();
window.speakeasyBridge = bridge;
if (bridge.embedded) {
  document.documentElement.classList.add('embedded');
}
```

- [ ] **Step 2: Add `base: './'` to `frontend/vite.config.ts`**

Inside `defineConfig({ ... })`, add `base: './',` as the first property (before `plugins`). Built asset URLs become relative so they resolve from `file://`.

- [ ] **Step 3: Append embedded-mode globals to `frontend/src/styles/tokens.css`**

```css
/* Embedded mode: the native window supplies blur (NSVisualEffectView) and
   chrome; the page paints only surfaces. Class set by bridge.ts. */
html.embedded body { background: transparent; }
html.embedded body::before { display: none; }
```

- [ ] **Step 4: GlassPanel embedded fill variant**

Append to `frontend/src/components/GlassPanel.module.css`:

```css
/* Inside a native window the panel IS the window: native corners/shadow,
   real blur behind — so no radius/border/backdrop-filter, lighter tint. */
.embeddedPanel {
  border-radius: 0;
  border: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.14);
  background: linear-gradient(180deg, rgba(42,46,48,0.30), rgba(22,25,26,0.34));
}
```

Replace `frontend/src/components/GlassPanel.tsx` with:

```tsx
import type { CSSProperties, ReactNode } from 'react';
import { bridge } from '../bridge';
import styles from './GlassPanel.module.css';

interface GlassPanelProps {
  width: number;
  height: number;
  children: ReactNode;
}

export function GlassPanel({ width, height, children }: GlassPanelProps) {
  const style: CSSProperties = bridge.embedded
    ? { width: '100vw', height: '100vh' }
    : { width, height };
  const className = bridge.embedded
    ? `${styles.panel} ${styles.embeddedPanel}`
    : styles.panel;
  return (
    <div className={className} style={style}>
      {children}
    </div>
  );
}
```

- [ ] **Step 5: TitleBar — hide drawn lights when embedded**

Append to `frontend/src/components/TitleBar.module.css` (native traffic lights overlay the same top-left area, so the drawn dots disappear and any title shifts right past them):

```css
:global(html.embedded) .light { display: none; }
:global(html.embedded) .title { margin-left: 62px; }
```

- [ ] **Step 6: StatusDot amber variant**

In `frontend/src/components/StatusDot.tsx` change the variant type:

```tsx
interface StatusDotProps {
  variant: 'green' | 'rec' | 'amber';
}
```

Append to `frontend/src/components/StatusDot.module.css`:

```css
.amber {
  background: #f0b34a;
  box-shadow: 0 0 7px 1px rgba(240,179,74,0.6);
}
```

- [ ] **Step 7: GlassButton onClick**

Replace `frontend/src/components/GlassButton.tsx` with:

```tsx
import type { ReactNode } from 'react';
import styles from './GlassButton.module.css';

interface GlassButtonProps {
  icon: ReactNode;
  label: string;
  dim?: boolean;
  onClick?: () => void;
}

export function GlassButton({ icon, label, dim = false, onClick }: GlassButtonProps) {
  return (
    <button className={dim ? `${styles.btn} ${styles.dim}` : styles.btn} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}
```

- [ ] **Step 8: Verify**

Run (in `frontend/`): `npm run build`
Expected: exits 0. Run: `grep -o 'src="\./assets[^"]*"' dist/dock.html` — expected: at least one match (relative base took effect).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/bridge.ts frontend/vite.config.ts frontend/src/styles/tokens.css frontend/src/components
git commit -m "Add JS bridge scaffold and embedded display mode"
```

---

### Task 2: Dock page — real engine state

**Files:**
- Modify: `frontend/src/dock/App.tsx` (full replacement below)
- Modify: `frontend/src/dock/App.module.css` (one addition)

**Interfaces:**
- Consumes: `bridge` (Task 1); `StatusDot` `amber` variant; `GlassButton` `onClick`.
- Produces: the page calls bridge methods `app.getState`, `app.beginMeeting`, `app.endMeeting`, `app.openWindow` (`{name: 'meetings' | 'training'}`), `app.quit`, and listens for event `state`. Payload shape (Python must match, Task 7):

```ts
interface AppState {
  mode: 'loading' | 'ready' | 'recording' | 'transcribing' | 'paused'
      | 'meeting_recording' | 'meeting_processing';
  profileName: string;
  elapsedSeconds: number;      // meeting elapsed at push time; 0 otherwise
  progressText: string | null; // meeting-processing progress line
}
```

- [ ] **Step 1: Append to `frontend/src/dock/App.module.css`**

```css
.progress {
  font: 500 11.5px/1 var(--sans);
  color: rgba(255,255,255,0.62);
}
```

- [ ] **Step 2: Replace `frontend/src/dock/App.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { AppIdentity } from '../components/AppIdentity';
import { StatusDot } from '../components/StatusDot';
import { PrimaryButton } from '../components/PrimaryButton';
import { GlassButton } from '../components/GlassButton';
import { Waveform } from '../components/Waveform';
import { bridge } from '../bridge';
import styles from './App.module.css';

type EngineMode =
  | 'loading' | 'ready' | 'recording' | 'transcribing' | 'paused'
  | 'meeting_recording' | 'meeting_processing';

interface AppState {
  mode: EngineMode;
  profileName: string;
  elapsedSeconds: number;
  progressText: string | null;
}

interface DockAppProps {
  operatorName?: string;
  readyMessage?: string;
}

const STATUS: Record<Exclude<EngineMode, 'meeting_recording'>, { text: string; dot: 'green' | 'rec' | 'amber' }> = {
  loading: { text: 'Loading model…', dot: 'amber' },
  ready: { text: 'Ready', dot: 'green' },
  recording: { text: 'Listening…', dot: 'rec' },
  transcribing: { text: 'Transcribing…', dot: 'amber' },
  paused: { text: 'Paused', dot: 'amber' },
  meeting_processing: { text: 'Processing meeting…', dot: 'amber' },
};

function formatTimer(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, '0');
  const seconds = (totalSeconds % 60).toString().padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function MeetingsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.6)" strokeWidth="1.7" strokeLinecap="round">
      <rect x="4" y="3.5" width="16" height="17" rx="2.5" />
      <line x1="8" y1="8" x2="16" y2="8" />
      <line x1="8" y1="12" x2="16" y2="12" />
      <line x1="8" y1="16" x2="13" y2="16" />
    </svg>
  );
}

function TrainIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.6)" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4" />
      <path d="M4.5 20c0-3.6 3.4-6 7.5-6s7.5 2.4 7.5 6" />
    </svg>
  );
}

export function DockApp({ operatorName = 'Jason', readyMessage = 'Ready' }: DockAppProps) {
  const [app, setApp] = useState<AppState>({
    mode: 'ready',
    profileName: operatorName,
    elapsedSeconds: 0,
    progressText: null,
  });
  const [tick, setTick] = useState(0);
  const baselineRef = useRef<number | null>(null);

  function applyState(next: AppState) {
    if (next.mode === 'meeting_recording') {
      baselineRef.current = Date.now() - next.elapsedSeconds * 1000;
    } else {
      baselineRef.current = null;
    }
    setApp(next);
  }

  useEffect(() => {
    if (!bridge.embedded) return;
    bridge.call<AppState>('app.getState').then(applyState).catch(() => {});
    return bridge.on('state', (payload) => applyState(payload as AppState));
  }, []);

  // Browser preview only: R toggles a fake meeting.
  useEffect(() => {
    if (bridge.embedded) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== 'r' && event.key !== 'R') return;
      setApp((current) => {
        const recording = current.mode === 'meeting_recording';
        baselineRef.current = recording ? null : Date.now();
        return { ...current, mode: recording ? 'ready' : 'meeting_recording', elapsedSeconds: 0 };
      });
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const isRecording = app.mode === 'meeting_recording';

  useEffect(() => {
    if (!isRecording) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [isRecording]);
  void tick;

  const elapsed = baselineRef.current === null
    ? 0
    : Math.max(0, Math.floor((Date.now() - baselineRef.current) / 1000));

  function onPrimary() {
    if (bridge.embedded) {
      void bridge.call(isRecording ? 'app.endMeeting' : 'app.beginMeeting');
    } else {
      setApp((current) => {
        const recording = current.mode === 'meeting_recording';
        baselineRef.current = recording ? null : Date.now();
        return { ...current, mode: recording ? 'ready' : 'meeting_recording', elapsedSeconds: 0 };
      });
    }
  }

  function openWindow(name: 'meetings' | 'training') {
    if (bridge.embedded) void bridge.call('app.openWindow', { name });
    else console.log('open', name);
  }

  function onQuit(event: React.MouseEvent) {
    event.preventDefault();
    if (bridge.embedded) void bridge.call('app.quit');
    else console.log('quit');
  }

  const idleInfo = isRecording ? null : STATUS[app.mode];
  const name = bridge.embedded ? app.profileName : operatorName;
  const primaryDisabled = !isRecording && app.mode !== 'ready';

  return (
    <GlassPanel width={360} height={300}>
      <TitleBar plain />
      <div className={styles.body}>
        <AppIdentity
          live={isRecording}
          status={
            isRecording ? (
              <>
                <StatusDot variant="rec" /> Recording meeting{' '}
                <span className={styles.timer}>· {formatTimer(elapsed)}</span>
              </>
            ) : (
              <>
                <StatusDot variant={idleInfo!.dot} />{' '}
                {app.mode === 'ready' ? (
                  <>
                    {bridge.embedded ? 'Ready' : readyMessage} —{' '}
                    <span className={styles.name}>{name}</span>
                  </>
                ) : app.mode === 'meeting_processing' ? (
                  <span className={styles.progress}>{app.progressText ?? idleInfo!.text}</span>
                ) : (
                  idleInfo!.text
                )}
              </>
            )
          }
        />

        {isRecording && <Waveform />}
        {isRecording && (
          <div className={styles.recStatus}>
            <span className={styles.pillBadge}>Dictation paused</span>
          </div>
        )}

        <PrimaryButton
          danger={isRecording}
          className={isRecording ? styles.primaryRecording : styles.primaryIdle}
          onClick={primaryDisabled ? undefined : onPrimary}
        >
          {isRecording ? 'End Meeting' : 'Begin Meeting'}
        </PrimaryButton>

        <div className={isRecording ? `${styles.row} ${styles.rowDisabled}` : styles.row}>
          <GlassButton icon={<MeetingsIcon />} label="Meetings" dim={isRecording} onClick={() => openWindow('meetings')} />
          <GlassButton icon={<TrainIcon />} label="Train Profile" dim={isRecording} onClick={() => openWindow('training')} />
        </div>

        <div className={styles.foot}>
          <a className={styles.quit} href="#" onClick={onQuit}>Quit Speakeasy</a>
        </div>
      </div>
    </GlassPanel>
  );
}
```

- [ ] **Step 3: Verify**

Run: `npm run build` — expected exit 0. Then `npm run dev`, open `/dock.html` in a browser: idle renders, `R` still toggles the fake meeting (preview preserved), timer ticks.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/dock
git commit -m "Wire dock page to bridge engine state"
```

---

### Task 3: Meetings page — real store data + structured speaker numbers

**Files:**
- Modify: `frontend/src/mock/meetings.ts` (TranscriptLine shape change)
- Modify: `frontend/src/meetings/App.tsx` (full replacement below)
- Modify: `frontend/src/meetings/App.module.css` (additions below)

**Interfaces:**
- Consumes: `bridge`; `ActionButton` (unchanged).
- Produces / expects from Python (Task 8): methods `meetings.list` → `MeetingMeta[]`, `meetings.get {id}` → `MeetingDetail`, `meetings.rename {id, title}` → updated `MeetingMeta[]`, `meetings.delete {id}` → updated `MeetingMeta[]`, `meetings.copy {id}` → `true`, `meetings.export {id}` → `true` (panel may be cancelled; still resolves `true`); event `meetings.changed` (no payload) when a transcript is saved. Shapes:

```ts
export interface TranscriptLine {
  time: string;          // "[HH:MM:SS]"
  speakerNumber: number; // 1-based, order of first appearance
  speakerLabel: string;  // e.g. "Speaker 1" (no colon)
  text: string;
}
export interface MeetingMeta {
  id: string; title: string; subtitle: string;
  date: string; duration: string; speakerCount: number;
}
export interface MeetingDetail extends MeetingMeta { lines: TranscriptLine[]; }
```

- [ ] **Step 1: Update `frontend/src/mock/meetings.ts`**

Replace the `TranscriptLine` interface, the `Meeting` interface, and the `line()` helper with:

```ts
export interface TranscriptLine {
  time: string;
  speakerNumber: number;
  speakerLabel: string;
  text: string;
}

export interface MeetingMeta {
  id: string;
  title: string;
  subtitle: string;
  date: string;
  duration: string;
  speakerCount: number;
}

export interface MeetingDetail extends MeetingMeta {
  lines: TranscriptLine[];
}

/** @deprecated kept as alias for phase-1 call sites */
export type Meeting = MeetingDetail;

function line(time: string, speakerNumber: number, text: string): TranscriptLine {
  return { time: `[${time}]`, speakerNumber, speakerLabel: `Speaker ${speakerNumber}`, text };
}
```

`SPEAKER_PALETTE`, `speakerColor`, and the three seeded `MEETINGS` entries are unchanged (the `line(...)` call sites already pass numbers; `MEETINGS`'s type annotation becomes `MeetingDetail[]`).

- [ ] **Step 2: Append to `frontend/src/meetings/App.module.css`**

```css
.renameInput {
  font: 600 19px/1.1 var(--sans);
  letter-spacing: -0.3px;
  color: rgba(255,255,255,0.97);
  background: rgba(0,0,0,0.28);
  border: 1.5px solid var(--coral-1);
  border-radius: 8px;
  padding: 2px 8px;
  outline: none;
  width: 100%;
}

.empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font: 400 13px/1.4 var(--sans);
  color: rgba(255,255,255,0.4);
}
```

- [ ] **Step 3: Replace `frontend/src/meetings/App.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { ActionButton } from '../components/ActionButton';
import { MEETINGS, speakerColor } from '../mock/meetings';
import type { MeetingDetail, MeetingMeta } from '../mock/meetings';
import { bridge } from '../bridge';
import styles from './App.module.css';

interface MeetingsAppProps {
  colorCodeSpeakers?: boolean;
}

export function MeetingsApp({ colorCodeSpeakers = true }: MeetingsAppProps) {
  const [list, setList] = useState<MeetingMeta[]>(bridge.embedded ? [] : MEETINGS);
  const [selectedId, setSelectedId] = useState<string | null>(bridge.embedded ? null : MEETINGS[0].id);
  const [detail, setDetail] = useState<MeetingDetail | null>(bridge.embedded ? null : MEETINGS[0]);
  const [renaming, setRenaming] = useState(false);
  const [renameText, setRenameText] = useState('');
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const confirmTimer = useRef<number | null>(null);

  function select(id: string | null, metas: MeetingMeta[]) {
    setRenaming(false);
    setConfirmingDelete(false);
    const target = id !== null && metas.some((m) => m.id === id) ? id : metas[0]?.id ?? null;
    setSelectedId(target);
    if (!bridge.embedded) {
      setDetail(MEETINGS.find((m) => m.id === target) ?? null);
      return;
    }
    if (target === null) {
      setDetail(null);
      return;
    }
    void bridge.call<MeetingDetail>('meetings.get', { id: target }).then(setDetail).catch(() => setDetail(null));
  }

  function refresh(preserveId: string | null) {
    void bridge.call<MeetingMeta[]>('meetings.list').then((metas) => {
      setList(metas);
      select(preserveId, metas);
    }).catch(() => {});
  }

  useEffect(() => {
    if (!bridge.embedded) return;
    refresh(null);
    return bridge.on('meetings.changed', () => refresh(selectedId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onDelete() {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
      confirmTimer.current = window.setTimeout(() => setConfirmingDelete(false), 4000);
      return;
    }
    setConfirmingDelete(false);
    if (bridge.embedded && selectedId !== null) {
      void bridge.call<MeetingMeta[]>('meetings.delete', { id: selectedId }).then((metas) => {
        setList(metas);
        select(null, metas);
      });
    } else {
      console.log('delete', selectedId);
    }
  }

  function commitRename() {
    setRenaming(false);
    const title = renameText.trim();
    if (title === '' || selectedId === null) return;
    if (bridge.embedded) {
      void bridge.call<MeetingMeta[]>('meetings.rename', { id: selectedId, title }).then((metas) => {
        setList(metas);
        select(selectedId, metas);
      });
    } else {
      console.log('rename', selectedId, title);
    }
  }

  const selectedMeta = list.find((m) => m.id === selectedId) ?? null;

  return (
    <GlassPanel width={720} height={480}>
      <TitleBar title="Meetings" />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Meetings</span>
          {list.map((m) => {
            const active = m.id === selectedId;
            return (
              <button
                key={m.id}
                className={active ? `${styles.item} ${styles.itemActive}` : styles.item}
                onClick={() => select(m.id, list)}
              >
                <div className={active ? `${styles.itemTitle} ${styles.itemTitleActive}` : styles.itemTitle}>
                  {m.title}
                </div>
                <div className={active ? `${styles.itemSub} ${styles.itemSubActive}` : styles.itemSub}>
                  {m.subtitle}
                </div>
              </button>
            );
          })}
        </div>
        <div className={styles.detail}>
          {selectedMeta === null || detail === null ? (
            <div className={styles.empty}>No meetings yet — record one from the dock panel.</div>
          ) : (
            <>
              {renaming ? (
                <input
                  className={styles.renameInput}
                  value={renameText}
                  autoFocus
                  onChange={(e) => setRenameText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename();
                    if (e.key === 'Escape') setRenaming(false);
                  }}
                  onBlur={() => setRenaming(false)}
                />
              ) : (
                <div className={styles.dTitle}>{detail.title}</div>
              )}
              <div className={styles.meta}>
                <span>{detail.date}</span>
                <span className={styles.sep}>•</span>
                <span>{detail.duration}</span>
                <span className={styles.sep}>•</span>
                <span>{detail.speakerCount} speakers</span>
              </div>
              <div className={styles.transcript}>
                <div className={styles.lines}>
                  {detail.lines.map((ln, index) => (
                    <div key={index}>
                      <span className={styles.time}>{ln.time}</span>{' '}
                      <span
                        className={styles.speaker}
                        style={{ color: speakerColor(ln.speakerNumber, colorCodeSpeakers) }}
                      >
                        {ln.speakerLabel}:
                      </span>{' '}
                      <span className={styles.text}>{ln.text}</span>
                    </div>
                  ))}
                </div>
                <div className={styles.fade} />
              </div>
              <div className={styles.actionRow}>
                <ActionButton
                  variant="strong"
                  onClick={() => {
                    if (bridge.embedded && selectedId !== null) void bridge.call('meetings.copy', { id: selectedId });
                    else console.log('copy');
                  }}
                >
                  Copy
                </ActionButton>
                <ActionButton
                  onClick={() => {
                    if (bridge.embedded && selectedId !== null) void bridge.call('meetings.export', { id: selectedId });
                    else console.log('export');
                  }}
                >
                  Export
                </ActionButton>
                <ActionButton
                  onClick={() => {
                    setRenameText(detail.title);
                    setRenaming(true);
                  }}
                >
                  Rename
                </ActionButton>
                <ActionButton variant="danger" className={styles.spacer} onClick={onDelete}>
                  {confirmingDelete ? 'Confirm delete' : 'Delete'}
                </ActionButton>
              </div>
            </>
          )}
        </div>
      </div>
    </GlassPanel>
  );
}
```

- [ ] **Step 4: Verify**

Run: `npm run build` — exit 0. `npm run dev`, open `/meetings.html`: mock list still renders, speaker colors correct (now via `speakerNumber`, no regex), Rename swaps the title to a coral-ring input, Delete becomes "Confirm delete" on first click and resets after 4s.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/mock/meetings.ts frontend/src/meetings
git commit -m "Wire meetings page to bridge; structured speaker numbers"
```

---

### Task 4: Training page — sessions + practice flow

**Files:**
- Modify: `frontend/src/training/App.tsx` (full replacement below)
- Modify: `frontend/src/training/App.module.css` (additions below)

**Interfaces:**
- Consumes: `bridge`.
- Produces / expects from Python (Task 9): methods `training.listSessions` → `SessionInfo[]`, `training.startSession {name}` → `true`, `training.practice {text}` → `true`; events `training.prompt {text, status}`, `training.feedback {line}`, `training.done {message}`. Shape:

```ts
interface SessionInfo { name: string; done: boolean; suggested: boolean; }
```

- [ ] **Step 1: Append to `frontend/src/training/App.module.css`**

```css
.phrase {
  margin: 14px 0 0;
  max-width: 360px;
  font: 600 22px/1.3 var(--sans);
  letter-spacing: -0.4px;
  color: rgba(255,255,255,0.95);
  text-wrap: balance;
}

.feedback {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font: 400 13px/1.4 var(--sans);
  color: rgba(255,255,255,0.62);
  white-space: pre-wrap;
}

.itemClickable {
  cursor: pointer;
  border: 0.5px solid transparent;
  background: transparent;
  text-align: left;
  width: 100%;
}

.itemClickable:hover {
  background: rgba(255,255,255,0.05);
  border-radius: 8px;
}
```

- [ ] **Step 2: Replace `frontend/src/training/App.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { bridge } from '../bridge';
import styles from './App.module.css';

interface TrainingAppProps {
  operatorName?: string;
}

interface SessionInfo {
  name: string;
  done: boolean;
  suggested: boolean;
}

interface PracticeState {
  status: string;
  phrase: string | null;
  feedback: string[];
  finished: boolean;
}

const MOCK_SESSIONS: SessionInfo[] = [
  { name: 'Everyday phrases', done: true, suggested: false },
  { name: 'Tech & jargon', done: true, suggested: false },
  { name: 'Names & proper nouns', done: true, suggested: false },
  { name: 'Numbers & units', done: false, suggested: true },
  { name: 'Tricky words & homophones', done: false, suggested: false },
];

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#E8955A" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

export function TrainingApp({ operatorName = 'Jason' }: TrainingAppProps) {
  const [sessions, setSessions] = useState<SessionInfo[]>(bridge.embedded ? [] : MOCK_SESSIONS);
  const [practiceText, setPracticeText] = useState('');
  const [practice, setPractice] = useState<PracticeState | null>(null);
  const [profileName, setProfileName] = useState(operatorName);

  function refreshSessions() {
    void bridge.call<{ sessions: SessionInfo[]; profileName: string }>('training.listSessions')
      .then(({ sessions: s, profileName: p }) => {
        setSessions(s);
        setProfileName(p);
      })
      .catch(() => {});
  }

  useEffect(() => {
    if (!bridge.embedded) return;
    refreshSessions();
    const offPrompt = bridge.on('training.prompt', (payload) => {
      const { text, status } = payload as { text: string; status: string };
      setPractice((current) => ({
        status,
        phrase: text,
        feedback: current?.feedback ?? [],
        finished: false,
      }));
    });
    const offFeedback = bridge.on('training.feedback', (payload) => {
      const { line } = payload as { line: string };
      setPractice((current) =>
        current === null
          ? null
          : { ...current, feedback: [...current.feedback.slice(-3), line] },
      );
    });
    const offDone = bridge.on('training.done', (payload) => {
      const { message } = payload as { message: string };
      setPractice((current) =>
        current === null
          ? { status: message, phrase: null, feedback: [], finished: true }
          : { ...current, status: message, phrase: null, finished: true },
      );
      refreshSessions();
    });
    return () => {
      offPrompt();
      offFeedback();
      offDone();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function startSession(name: string) {
    if (bridge.embedded) {
      setPractice({ status: 'Starting session…', phrase: null, feedback: [], finished: false });
      void bridge.call('training.startSession', { name }).catch(() => setPractice(null));
    } else {
      console.log('start session', name);
    }
  }

  function startPractice() {
    const term = practiceText.trim();
    if (term === '') return;
    setPracticeText('');
    if (bridge.embedded) {
      setPractice({ status: 'Starting…', phrase: null, feedback: [], finished: false });
      void bridge.call('training.practice', { text: term }).catch(() => setPractice(null));
    } else {
      console.log('practice', term);
    }
  }

  return (
    <GlassPanel width={640} height={440}>
      <TitleBar title={<>Training — <strong>{profileName}</strong></>} />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Sessions</span>
          {sessions.map((s) => (
            <button
              key={s.name}
              className={
                s.suggested
                  ? `${styles.item} ${styles.itemCurrent} ${styles.itemClickable}`
                  : `${styles.item} ${styles.itemClickable}`
              }
              onClick={() => startSession(s.name)}
            >
              {s.done ? <CheckIcon /> : <span className={styles.bullet} />}
              <span className={s.done ? styles.name : `${styles.name} ${styles.nameTrunc}`}>{s.name}</span>
              {s.suggested && <span className={styles.star}>★</span>}
            </button>
          ))}
        </div>
        <div className={styles.detail}>
          {practice === null ? (
            <>
              <span className={styles.intro}>Short read-aloud sessions teach Speakeasy your words.</span>
              <h2 className={styles.headline}>Pick a session on the left, or practice your own words below.</h2>
            </>
          ) : (
            <>
              <span className={styles.intro}>{practice.status}</span>
              {practice.phrase !== null && <h2 className={styles.phrase}>“{practice.phrase}”</h2>}
              {practice.feedback.length > 0 && (
                <div className={styles.feedback}>
                  {practice.feedback.map((line, i) => (
                    <span key={i}>{line}</span>
                  ))}
                </div>
              )}
            </>
          )}
          <div className={styles.inputRow}>
            <input
              className={styles.textInput}
              type="text"
              placeholder="Your own word or phrase…"
              value={practiceText}
              onChange={(event) => setPracticeText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') startPractice();
              }}
            />
            <button className={styles.practiceBtn} onClick={startPractice}>
              Practice
            </button>
          </div>
        </div>
      </div>
    </GlassPanel>
  );
}
```

- [ ] **Step 3: Verify**

Run: `npm run build` — exit 0. `npm run dev`, open `/training.html`: mock sidebar matches phase 1 (3 checked, starred suggestion, truncated last item), right pane unchanged empty state, input/Practice work as stubs.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/training
git commit -m "Wire training page to bridge with practice flow states"
```

---

### Task 5: `webbridge.py` — pure bridge logic + tests

**Files:**
- Create: `speakeasy/ui/webbridge.py`
- Test: `tests/test_webbridge.py`

**Interfaces:**
- Consumes: `speakeasy.meetings.MeetingSegment` (only its `.speaker/.start/.text` attributes — tests use a stand-in namedtuple).
- Produces (imported by `webwindow.py` and controllers):
  - `BridgeDispatcher` — `register(method: str, handler)` where `handler(params: dict, respond)` and `respond(result=None, error=None)` must be called exactly once; `dispatch(message: dict, send_js: Callable[[str], None])` validates `{id, method, params}`, wraps `respond` to serialize `speakeasyBridge._resolve/_reject` JS and pass it to `send_js`.
  - `EvalQueue` — `add(js: str)`, `mark_ready(flush: Callable[[str], None])`, `send(js, flush)`: buffers JS strings until ready, then passes each to `flush`.
  - `segments_to_lines(segments) -> list[dict]` — `[{time, speakerNumber, speakerLabel, text}]`, speaker numbers 1-based by order of first appearance.
  - `format_timestamp(seconds: float) -> str` — `"[HH:MM:SS]"`.

**No AppKit/WebKit imports in this module** — it must be importable under plain pytest.

- [ ] **Step 1: Write the failing tests — `tests/test_webbridge.py`**

```python
"""Pure-logic tests for the JS bridge: dispatch, eval queueing, formatting."""

import json
from collections import namedtuple

import pytest

from speakeasy.ui.webbridge import (
    BridgeDispatcher,
    EvalQueue,
    format_timestamp,
    segments_to_lines,
)

Seg = namedtuple("Seg", "speaker start end text")


def _parse_js(js):
    """Extract (kind, id, payload) from a _resolve/_reject JS string."""
    assert js.startswith("window.speakeasyBridge && window.speakeasyBridge.")
    body = js.split("window.speakeasyBridge.", 1)[1]
    kind, rest = body.split("(", 1)
    args = rest.rsplit(")", 1)[0]
    call_id, payload = args.split(",", 1)
    return kind, int(call_id), json.loads(payload)


class TestDispatcher:
    def test_routes_to_registered_handler_and_resolves(self):
        d = BridgeDispatcher()
        d.register("math.add", lambda params, respond: respond(params["a"] + params["b"]))
        sent = []
        d.dispatch({"id": 7, "method": "math.add", "params": {"a": 2, "b": 3}}, sent.append)
        kind, call_id, payload = _parse_js(sent[0])
        assert (kind, call_id, payload) == ("_resolve", 7, 5)

    def test_unknown_method_rejects(self):
        d = BridgeDispatcher()
        sent = []
        d.dispatch({"id": 1, "method": "nope", "params": {}}, sent.append)
        kind, call_id, payload = _parse_js(sent[0])
        assert kind == "_reject"
        assert call_id == 1
        assert "nope" in payload

    def test_handler_exception_rejects(self):
        d = BridgeDispatcher()

        def boom(params, respond):
            raise RuntimeError("kapow")

        d.register("boom", boom)
        sent = []
        d.dispatch({"id": 2, "method": "boom", "params": {}}, sent.append)
        kind, _, payload = _parse_js(sent[0])
        assert kind == "_reject"
        assert "kapow" in payload

    def test_handler_error_via_respond(self):
        d = BridgeDispatcher()
        d.register("bad", lambda params, respond: respond(error="not now"))
        sent = []
        d.dispatch({"id": 3, "method": "bad", "params": {}}, sent.append)
        kind, _, payload = _parse_js(sent[0])
        assert (kind, payload) == ("_reject", "not now")

    def test_deferred_respond(self):
        d = BridgeDispatcher()
        held = {}
        d.register("later", lambda params, respond: held.setdefault("respond", respond))
        sent = []
        d.dispatch({"id": 4, "method": "later", "params": {}}, sent.append)
        assert sent == []
        held["respond"]({"ok": True})
        kind, call_id, payload = _parse_js(sent[0])
        assert (kind, call_id, payload) == ("_resolve", 4, {"ok": True})

    def test_respond_twice_ignored(self):
        d = BridgeDispatcher()

        def double(params, respond):
            respond(1)
            respond(2)

        d.register("double", double)
        sent = []
        d.dispatch({"id": 5, "method": "double", "params": {}}, sent.append)
        assert len(sent) == 1

    def test_malformed_message_ignored(self):
        d = BridgeDispatcher()
        sent = []
        d.dispatch({"method": "x"}, sent.append)      # no id
        d.dispatch("not a dict", sent.append)
        assert sent == []


class TestEvalQueue:
    def test_buffers_until_ready(self):
        q = EvalQueue()
        out = []
        q.send("a()", out.append)
        q.send("b()", out.append)
        assert out == []
        q.mark_ready(out.append)
        assert out == ["a()", "b()"]
        q.send("c()", out.append)
        assert out == ["a()", "b()", "c()"]


class TestFormatting:
    def test_timestamp(self):
        assert format_timestamp(0) == "[00:00:00]"
        assert format_timestamp(75.9) == "[00:01:15]"
        assert format_timestamp(3725) == "[01:02:05]"

    def test_speaker_numbers_by_first_appearance(self):
        segs = [
            Seg("Speaker 2", 0.0, 4.0, "hello"),
            Seg("Speaker 1", 5.0, 8.0, "hi"),
            Seg("Speaker 2", 9.0, 12.0, "again"),
        ]
        lines = segments_to_lines(segs)
        assert [l["speakerNumber"] for l in lines] == [1, 2, 1]
        assert lines[0] == {
            "time": "[00:00:00]",
            "speakerNumber": 1,
            "speakerLabel": "Speaker 2",
            "text": "hello",
        }

    def test_non_numeric_labels_supported(self):
        segs = [Seg("Alice", 0.0, 1.0, "a"), Seg("Bob", 2.0, 3.0, "b")]
        lines = segments_to_lines(segs)
        assert [l["speakerNumber"] for l in lines] == [1, 2]
        assert [l["speakerLabel"] for l in lines] == ["Alice", "Bob"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_webbridge.py -q`
Expected: collection error — `No module named 'speakeasy.ui.webbridge'`.

- [ ] **Step 3: Create `speakeasy/ui/webbridge.py`**

```python
"""Pure-Python half of the JS bridge for WKWebView-hosted windows.

No AppKit/WebKit imports here — this module carries the logic that unit
tests exercise (message dispatch, pre-load eval queueing, transcript
formatting). The ObjC glue lives in webwindow.py.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def _js_call(func: str, call_id: int, payload: Any) -> str:
    return (
        "window.speakeasyBridge && window.speakeasyBridge."
        f"{func}({call_id}, {json.dumps(payload)})"
    )


class BridgeDispatcher:
    """Routes {id, method, params} messages to registered handlers.

    Handlers receive (params, respond); respond(result=None, error=None)
    must be called exactly once — synchronously or later (e.g. from a
    save-panel completion handler). Extra calls are ignored.
    """

    def __init__(self) -> None:
        self._methods: dict[str, Callable[[dict, Callable], None]] = {}

    def register(self, method: str, handler: Callable[[dict, Callable], None]) -> None:
        self._methods[method] = handler

    def dispatch(self, message: Any, send_js: Callable[[str], None]) -> None:
        if not isinstance(message, dict):
            return
        call_id = message.get("id")
        method = message.get("method")
        if not isinstance(call_id, int) or not isinstance(method, str):
            return
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}

        answered = False

        def respond(result: Any = None, error: str | None = None) -> None:
            nonlocal answered
            if answered:
                return
            answered = True
            if error is not None:
                send_js(_js_call("_reject", call_id, str(error)))
            else:
                send_js(_js_call("_resolve", call_id, result))

        handler = self._methods.get(method)
        if handler is None:
            respond(error=f"unknown method: {method}")
            return
        try:
            handler(params, respond)
        except Exception as exc:  # surface handler bugs to the page, don't crash the app
            respond(error=f"{type(exc).__name__}: {exc}")


class EvalQueue:
    """Buffers evaluateJavaScript payloads until the page has loaded.

    evaluateJavaScript before didFinishNavigation is silently dropped by
    WebKit — early engine-state pushes would vanish without this.
    """

    def __init__(self) -> None:
        self._ready = False
        self._pending: list[str] = []

    def send(self, js: str, flush: Callable[[str], None]) -> None:
        if self._ready:
            flush(js)
        else:
            self._pending.append(js)

    def mark_ready(self, flush: Callable[[str], None]) -> None:
        self._ready = True
        pending, self._pending = self._pending, []
        for js in pending:
            flush(js)


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"[{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}]"


def segments_to_lines(segments) -> list[dict]:
    """MeetingSegment(speaker, start, end, text) -> page TranscriptLine dicts.

    Speaker numbers are 1-based in order of first appearance, keyed on the
    label — robust to any label format the diarizer produces.
    """
    order: dict[str, int] = {}
    lines: list[dict] = []
    for seg in segments:
        number = order.setdefault(seg.speaker, len(order) + 1)
        lines.append(
            {
                "time": format_timestamp(seg.start),
                "speakerNumber": number,
                "speakerLabel": seg.speaker,
                "text": seg.text,
            }
        )
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_webbridge.py -q`
Expected: all pass. Then run the full suite: `.venv/bin/python -m pytest -q` — no regressions.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/ui/webbridge.py tests/test_webbridge.py
git commit -m "Add pure bridge logic: dispatcher, eval queue, transcript formatting"
```

---

### Task 6: `webwindow.py` + `settings.frontend_dist_path()`

**Files:**
- Create: `speakeasy/ui/webwindow.py`
- Modify: `speakeasy/settings.py` (add one function)

**Interfaces:**
- Consumes: `glass.make_glass_window`, `BridgeDispatcher`/`EvalQueue` (Task 5), `settings.frontend_dist_path()`.
- Produces: `WebWindow(title: str, width: float, height: float, page: str, dispatcher: BridgeDispatcher)` with:
  - `.window` — the NSWindow (callers set delegates, appearance is set inside).
  - `.emit(event: str, payload)` — main-thread only; queues until page load.
  - `.eval_js(js: str)` — main-thread only; queued send (used by dispatcher responses).
  - `.show()` — activate + `makeKeyAndOrderFront_`.

- [ ] **Step 1: Add to `speakeasy/settings.py`** (next to `diarization_model_dir()`, same pattern):

```python
def frontend_dist_path() -> Path:
    """Built web UI: bundled Resources/frontend, or frontend/dist from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "Resources" / "frontend"
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"
```

(Confirm `sys` and `Path` are already imported in settings.py — they are, per the existing `model_path()`.)

- [ ] **Step 2: Create `speakeasy/ui/webwindow.py`**

```python
"""WKWebView host for the glass windows.

WebKit/AppKit are imported lazily inside WebWindow so this module stays
importable in tests (same pattern as diarizer.py's lazy sherpa_onnx).
All methods are main-thread only — callers hop with
performSelectorOnMainThread first, exactly like the existing windows.
"""

from __future__ import annotations

import json

from speakeasy import settings
from speakeasy.ui.webbridge import BridgeDispatcher, EvalQueue

_handler_classes = None


def _objc_to_py(obj):
    """Recursively convert NSDictionary/NSArray/NSNumber/NSString to Python."""
    if isinstance(obj, dict) or hasattr(obj, "allKeys"):
        return {str(k): _objc_to_py(obj[k]) for k in obj}
    if isinstance(obj, (list, tuple)) or (
        hasattr(obj, "count") and hasattr(obj, "objectAtIndex_")
    ):
        return [_objc_to_py(x) for x in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    if hasattr(obj, "doubleValue"):
        value = obj.doubleValue()
        return int(value) if value == int(value) else value
    return str(obj)


def _make_handler_classes():
    """Define the ObjC helper classes exactly once (class names are global)."""
    global _handler_classes
    if _handler_classes is not None:
        return _handler_classes

    from Foundation import NSObject

    class SpeakeasyScriptHandler(NSObject):
        def initWithOwner_(self, owner):
            self = self.init()
            if self is None:
                return None
            self.owner = owner
            return self

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            self.owner._on_message(_objc_to_py(message.body()))

    class SpeakeasyNavDelegate(NSObject):
        def initWithOwner_(self, owner):
            self = self.init()
            if self is None:
                return None
            self.owner = owner
            return self

        def webView_didFinishNavigation_(self, webview, navigation):
            self.owner._on_page_loaded()

    _handler_classes = (SpeakeasyScriptHandler, SpeakeasyNavDelegate)
    return _handler_classes


class WebWindow:
    def __init__(
        self,
        title: str,
        width: float,
        height: float,
        page: str,
        dispatcher: BridgeDispatcher,
    ) -> None:
        from AppKit import (
            NSAppearance,
            NSAppearanceNameDarkAqua,
            NSViewHeightSizable,
            NSViewWidthSizable,
        )
        from Foundation import NSMakeRect, NSURL
        from WebKit import WKWebView, WKWebViewConfiguration

        from speakeasy.ui import glass

        script_handler_cls, nav_delegate_cls = _make_handler_classes()

        self._dispatcher = dispatcher
        self._queue = EvalQueue()

        window, effect = glass.make_glass_window(title, width, height)
        window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self.window = window

        config = WKWebViewConfiguration.alloc().init()
        self._script_handler = script_handler_cls.alloc().initWithOwner_(self)
        config.userContentController().addScriptMessageHandler_name_(
            self._script_handler, "speakeasy"
        )

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, width, height), config
        )
        # Transparent webview: the NSVisualEffectView behind supplies the blur;
        # the page paints only its semi-transparent surfaces on top.
        webview.setValue_forKey_(False, "drawsBackground")
        webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._nav_delegate = nav_delegate_cls.alloc().initWithOwner_(self)
        webview.setNavigationDelegate_(self._nav_delegate)
        effect.addSubview_(webview)
        self._webview = webview

        dist = settings.frontend_dist_path()
        page_path = dist / f"{page}.html"
        if not page_path.is_file():
            raise FileNotFoundError(
                f"frontend page missing: {page_path} — run `npm --prefix frontend run build`"
            )
        page_url = NSURL.fileURLWithPath_(str(page_path))
        # Directory-level read access so code-split assets under dist/ resolve.
        root_url = NSURL.fileURLWithPath_isDirectory_(str(dist), True)
        webview.loadFileURL_allowingReadAccessToURL_(page_url, root_url)

    # -- main-thread API ---------------------------------------------------

    def show(self) -> None:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def eval_js(self, js: str) -> None:
        self._queue.send(js, self._flush)

    def emit(self, event: str, payload=None) -> None:
        self.eval_js(
            "window.speakeasyBridge && window.speakeasyBridge._emit("
            f"{json.dumps(event)}, {json.dumps(payload)})"
        )

    # -- callbacks from the ObjC helpers ------------------------------------

    def _on_message(self, message) -> None:
        self._dispatcher.dispatch(message, self.eval_js)

    def _on_page_loaded(self) -> None:
        self._queue.mark_ready(self._flush)

    def _flush(self, js: str) -> None:
        self._webview.evaluateJavaScript_completionHandler_(js, None)
```

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -m pytest -q` — still green (webwindow untested directly, but must not break imports).
Run: `.venv/bin/python -c "import speakeasy.ui.webwindow; print('importable')"` — expected: `importable` (lazy imports keep it loadable even without WebKit installed yet; the WebKit import only runs when a WebWindow is constructed).

- [ ] **Step 4: Commit**

```bash
git add speakeasy/ui/webwindow.py speakeasy/settings.py
git commit -m "Add WKWebView glass window host with queued-eval bridge"
```

---

### Task 7: Dock controller — rewrite `main_window.py`

**Files:**
- Modify: `speakeasy/ui/main_window.py` (full replacement)

**Interfaces:**
- Consumes: `WebWindow`, `BridgeDispatcher`; engine API (`engine.state`, `engine.profile`, `engine.begin_meeting()`, `engine.end_meeting()`, `engine.shutdown()`, `engine.meeting_recorder.elapsed_seconds`); `State` enum from `speakeasy.engine`.
- Produces: `MainWindowController` with the SAME public surface `menubar.py` already uses: `alloc().initWithEngine_(engine)`, `.show()`, `windowWillClose_`, and main-thread selectors `engineStateChanged_(state_value)`, `meetingProgress_(text)`, `meetingSaved_(meeting_id)`. Also `.meetings_window` / `.training_window` attributes (lazily built controllers, reused by Task 8/9 classes).

- [ ] **Step 1: Replace `speakeasy/ui/main_window.py`**

Before writing, read the current file once to lift: the exact `openTraining_` profile guard and any `quitApp_` details beyond `engine.shutdown()` + terminate. Then replace with:

```python
"""Dock-reachable main window — WKWebView hosting frontend dock.html.

Same public surface as the old native implementation: menubar.py wires
engine callbacks to the engineStateChanged_/meetingProgress_/meetingSaved_
selectors on the main thread, and AppDelegate calls show() on launch and
Dock reopen.
"""

from Foundation import NSObject

from speakeasy.engine import State
from speakeasy.ui.webbridge import BridgeDispatcher
from speakeasy.ui.webwindow import WebWindow


class MainWindowController(NSObject):
    def initWithEngine_(self, engine):
        self = self.init()
        if self is None:
            return None
        self.engine = engine
        self.meetings_window = None
        self.training_window = None
        self._progress_text = None

        dispatcher = BridgeDispatcher()
        dispatcher.register("app.getState", self._get_state)
        dispatcher.register("app.beginMeeting", self._begin_meeting)
        dispatcher.register("app.endMeeting", self._end_meeting)
        dispatcher.register("app.openWindow", self._open_window)
        dispatcher.register("app.quit", self._quit)
        self._web = WebWindow("Speakeasy", 360, 300, "dock", dispatcher)
        self._web.window.setDelegate_(self)
        return self

    # -- lifecycle -----------------------------------------------------------

    def show(self):
        self._push_state()
        self._web.show()

    def windowWillClose_(self, notification):
        pass  # closing hides the window; the engine keeps running

    # -- engine callbacks (arrive on main thread via performSelector) ---------

    def engineStateChanged_(self, state_value):
        if State(str(state_value)) is not State.MEETING_PROCESSING:
            self._progress_text = None
        self._push_state()

    def meetingProgress_(self, text):
        self._progress_text = str(text)
        self._push_state()

    def meetingSaved_(self, meeting_id):
        self._push_state()
        if self.meetings_window is not None:
            self.meetings_window.meetingSaved_(meeting_id)

    # -- bridge handlers (main thread) ----------------------------------------

    def _state_payload(self):
        state = self.engine.state
        elapsed = 0.0
        recorder = getattr(self.engine, "meeting_recorder", None)
        if state is State.MEETING_RECORDING and recorder is not None:
            elapsed = float(recorder.elapsed_seconds)
        profile = self.engine.profile
        return {
            "mode": state.value,
            "profileName": profile.name if profile else "Guest",
            "elapsedSeconds": elapsed,
            "progressText": self._progress_text,
        }

    def _push_state(self):
        self._web.emit("state", self._state_payload())

    def _get_state(self, params, respond):
        respond(self._state_payload())

    def _begin_meeting(self, params, respond):
        self.engine.begin_meeting()  # submits to control internally
        respond(True)

    def _end_meeting(self, params, respond):
        self.engine.end_meeting()
        respond(True)

    def _open_window(self, params, respond):
        name = params.get("name")
        if name == "meetings":
            if self.meetings_window is None:
                from speakeasy.ui.meetings_window import MeetingsWindowController

                self.meetings_window = MeetingsWindowController.alloc().init()
            self.meetings_window.show()
        elif name == "training":
            if self.engine.profile is not None and self.engine.transcriber is not None:
                if self.training_window is None:
                    from speakeasy.ui.training_window import TrainingWindowController

                    self.training_window = TrainingWindowController.alloc().initWithEngine_(
                        self.engine
                    )
                self.training_window.showForProfile_(self.engine.profile)
        respond(True)

    def _quit(self, params, respond):
        respond(True)
        from AppKit import NSApplication

        self.engine.shutdown()
        NSApplication.sharedApplication().terminate_(None)
```

**Implementer note:** compare against the OLD file's `openMeetings_`/`openTraining_`/`quitApp_` before deleting them — if the old guards differ (e.g. training also checks `engine.transcriber`), keep the old guard logic. If `menubar.py` calls `openMeetings_`/`openTraining_`/`toggleMeeting_`/`quitApp_` selectors on this controller anywhere, keep thin selector wrappers delegating to the new handlers (check with `grep -n "main_window\." speakeasy/ui/menubar.py`).

- [ ] **Step 2: Verify**

Run: `.venv/bin/python -m pytest -q` — green.
Run: `grep -rn "MainWindowController\|main_window\." speakeasy/ui/menubar.py` and confirm every selector menubar.py invokes on `main_window` still exists (`show`, `engineStateChanged_`, `meetingProgress_`, `meetingSaved_` — plus any found by the grep; add wrappers if needed).

- [ ] **Step 3: Commit**

```bash
git add speakeasy/ui/main_window.py
git commit -m "Rewrite dock window as WKWebView host"
```

---

### Task 8: Meetings controller — rewrite `meetings_window.py`

**Files:**
- Modify: `speakeasy/ui/meetings_window.py` (full replacement)

**Interfaces:**
- Consumes: `WebWindow`, `BridgeDispatcher`, `segments_to_lines` (Task 5); `speakeasy.meetings` (`list_meetings`, `Meeting.load`, `.rename`, `.delete`, `render_txt`, `render_md`); `speakeasy.injector.set_clipboard`.
- Produces: `MeetingsWindowController` — `alloc().init()`, `.show()`, and a new main-thread selector `meetingSaved_(meeting_id)` that emits `meetings.changed` to the page (called by Task 7's forwarding; menubar's own cached instance refreshes on `show()`, same as the old `reload()`-on-show behavior).

- [ ] **Step 1: Replace `speakeasy/ui/meetings_window.py`**

```python
"""Meetings transcript browser — WKWebView hosting frontend meetings.html."""

from datetime import datetime

from Foundation import NSObject

from speakeasy import meetings
from speakeasy.ui.webbridge import BridgeDispatcher, segments_to_lines
from speakeasy.ui.webwindow import WebWindow


def _meeting_meta(meeting) -> dict:
    created = meeting.created
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    minutes = max(1, round(meeting.duration_seconds / 60))
    speaker_count = len({segment.speaker for segment in meeting.segments})
    return {
        "id": meeting.id,
        "title": meeting.title,
        "subtitle": f"{minutes} min · {speaker_count} speakers",
        "date": created.strftime("%b %-d, %Y · %H:%M"),
        "duration": f"{minutes} min",
        "speakerCount": speaker_count,
    }


class MeetingsWindowController(NSObject):
    def init(self):
        self = super().init()
        if self is None:
            return None
        dispatcher = BridgeDispatcher()
        dispatcher.register("meetings.list", self._list)
        dispatcher.register("meetings.get", self._get)
        dispatcher.register("meetings.rename", self._rename)
        dispatcher.register("meetings.delete", self._delete)
        dispatcher.register("meetings.copy", self._copy)
        dispatcher.register("meetings.export", self._export)
        self._web = WebWindow("Meetings", 720, 480, "meetings", dispatcher)
        self._web.window.setDelegate_(self)
        return self

    def show(self):
        self._web.emit("meetings.changed")  # page re-lists (old reload()-on-show)
        self._web.show()

    def windowWillClose_(self, notification):
        pass

    def meetingSaved_(self, meeting_id):
        self._web.emit("meetings.changed")

    # -- bridge handlers (main thread; store I/O is small local JSON,
    #    same main-thread pattern the old native window used) ----------------

    def _list(self, params, respond):
        respond([_meeting_meta(m) for m in meetings.list_meetings()])

    def _load(self, params):
        return meetings.Meeting.load(str(params.get("id", "")))

    def _get(self, params, respond):
        meeting = self._load(params)
        detail = _meeting_meta(meeting)
        detail["lines"] = segments_to_lines(meeting.segments)
        respond(detail)

    def _rename(self, params, respond):
        meeting = self._load(params)
        meeting.rename(str(params.get("title", "")))
        self._list({}, respond)

    def _delete(self, params, respond):
        self._load(params).delete()
        self._list({}, respond)

    def _copy(self, params, respond):
        from speakeasy import injector

        injector.set_clipboard(meetings.render_txt(self._load(params)))
        respond(True)

    def _export(self, params, respond):
        from AppKit import NSModalResponseOK, NSSavePanel

        meeting = self._load(params)
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["txt", "md"])
        panel.setNameFieldStringValue_(f"{meeting.title}.txt")

        def completion(response):
            if response == NSModalResponseOK:
                path = str(panel.URL().path())
                render = meetings.render_md if path.endswith(".md") else meetings.render_txt
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(render(meeting))
            respond(True)

        panel.beginSheetModalForWindow_completionHandler_(self._web.window, completion)
```

**Implementer note:** check the old file's `injector.set_clipboard` import path and `render_txt`/`render_md` usage and match them exactly (`grep -n "set_clipboard\|render_" speakeasy/ui/meetings_window.py` before replacing). If `meetings.Meeting.load` raises (bad id), the dispatcher converts it to a `_reject` automatically — that replaces the old NSAlert `_error()` path.

- [ ] **Step 2: Verify**

Run: `.venv/bin/python -m pytest -q` — green.
Run: `grep -rn "MeetingsWindowController" speakeasy/ui/menubar.py speakeasy/ui/main_window.py` — confirm all call sites use only `alloc().init()` / `.show()` (and Task 7's `meetingSaved_`).

- [ ] **Step 3: Commit**

```bash
git add speakeasy/ui/meetings_window.py
git commit -m "Rewrite meetings window as WKWebView host"
```

---

### Task 9: Training controller — rewrite `training_window.py`

**Files:**
- Modify: `speakeasy/ui/training_window.py` (full replacement; lift specified pieces from the old file first)

**Interfaces:**
- Consumes: `WebWindow`, `BridgeDispatcher`; `speakeasy.training` (`_TakeRecorder`, `_run_session`, `_train_prompt`, `_suggested_index`); `speakeasy.training_content.SESSIONS`; engine (`pause()`, `resume()`, `recorder`, `transcriber`, `worker`, `control`); `profile` (`sessions_done`, `add_word`).
- Produces: `TrainingWindowController` — `alloc().initWithEngine_(engine)`, `.showForProfile_(profile)`, `windowWillClose_` teardown. Emits `training.prompt {text, status}`, `training.feedback {line}`, `training.done {message}`; handles `training.listSessions` → `{sessions: [{name, done, suggested}], profileName}`, `training.startSession {name}`, `training.practice {text}`.

- [ ] **Step 1: Read the old `speakeasy/ui/training_window.py` FIRST** and record verbatim: (a) how `hotkey_name` is computed, (b) the exact `record` closure body (calls `taker.record()` across executors, ~lines 313-317), (c) the exact `_TakeRecorder(...)` constructor arguments and enter/exit + `unblock()` teardown sequence in `showForProfile_`/`windowWillClose_` (~lines 248-281), (d) the exact user-facing strings in the `_WindowUI` adapter (prompt status line, feedback lines, session-done line). These are re-used verbatim below where marked `# LIFT`.

- [ ] **Step 2: Replace `speakeasy/ui/training_window.py`**

```python
"""Voice-training window — WKWebView hosting frontend training.html.

Lifecycle is identical to the old native window: opening pauses dictation
and arms a _TakeRecorder; closing tears it down and resumes. Session and
practice flows run on a background thread driving a TrainingUI adapter
whose callbacks hop to the main thread and become bridge events.
"""

import threading

from Foundation import NSObject

from speakeasy import training, training_content
from speakeasy.ui.webbridge import BridgeDispatcher
from speakeasy.ui.webwindow import WebWindow


class _WebTrainingUI:
    """TrainingUI adapter: marshals callbacks to the main thread, then emits
    bridge events. controller is the TrainingWindowController (NSObject)."""

    def __init__(self, controller):
        self._c = controller

    # Each method mirrors the old _WindowUI adapter, replacing label updates
    # with bridge events. Strings must match the old adapter's verbatim (LIFT).

    def show_prompt(self, text, hotkey_name):
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiPrompt:",
            {"text": text, "status": f"Hold {hotkey_name}, read the line aloud, release when done."},
            False,
        )

    def _feedback(self, line):
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiFeedback:", line, False
        )

    def nothing_heard(self):
        self._feedback("Nothing heard — try again.")  # LIFT exact string

    def heard_correctly(self):
        self._feedback("Heard correctly.")  # LIFT exact string

    def correction_saved(self, heard_span, target):
        self._feedback(f"Learned: “{heard_span}” → “{target}”")  # LIFT exact string

    def correction_conflict(self, heard_span, target):
        self._feedback(f"Conflict: “{heard_span}” already maps elsewhere")  # LIFT exact string

    def session_finished(self, name, saved):
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiDone:", f"Session complete — {saved} correction(s) learned.", False
        )


class TrainingWindowController(NSObject):
    def initWithEngine_(self, engine):
        self = self.init()
        if self is None:
            return None
        self.engine = engine
        self.profile = None
        self._taker = None
        self._busy = False
        dispatcher = BridgeDispatcher()
        dispatcher.register("training.listSessions", self._list_sessions)
        dispatcher.register("training.startSession", self._start_session)
        dispatcher.register("training.practice", self._practice)
        self._web = WebWindow("Training", 640, 440, "training", dispatcher)
        self._web.window.setDelegate_(self)
        return self

    # -- lifecycle (LIFT the old showForProfile_/windowWillClose_ bodies:
    #    engine.pause(), _TakeRecorder(...) construction + __enter__ on open;
    #    unblock() + __exit__ + engine.resume() on close) ---------------------

    def showForProfile_(self, profile):
        if profile is None or self.engine.transcriber is None:
            return
        self.profile = profile
        if self._taker is None:
            self.engine.pause()
            self._taker = training._TakeRecorder(  # LIFT exact ctor args from old file
                self.engine.recorder,
                self.engine.transcriber,
                self.engine.worker,
                self.engine.control,
                None,  # LIFT: the old file's play_sound argument
            )
            self._taker.__enter__()
        self._push_sessions()
        self._web.show()

    def windowWillClose_(self, notification):
        if self._taker is not None:
            self._taker.unblock()
            self._taker.__exit__(None, None, None)
            self._taker = None
            self.engine.resume()
        self._busy = False

    # -- main-thread UI event relays ------------------------------------------

    def uiPrompt_(self, payload):
        self._web.emit("training.prompt", dict(payload))

    def uiFeedback_(self, line):
        self._web.emit("training.feedback", {"line": str(line)})

    def uiDone_(self, message):
        self._busy = False
        self._web.emit("training.done", {"message": str(message)})
        self._push_sessions()

    # -- bridge handlers --------------------------------------------------------

    def _sessions_payload(self):
        done = set(self.profile.sessions_done) if self.profile else set()
        suggested = training._suggested_index(self.profile) if self.profile else None
        sessions = []
        for index, session in enumerate(training_content.SESSIONS):
            sessions.append(
                {
                    "name": session["name"],
                    "done": session["name"] in done,
                    "suggested": index == suggested,
                }
            )
        return {
            "sessions": sessions,
            "profileName": self.profile.name if self.profile else "Guest",
        }

    def _push_sessions(self):
        self._web.emit("training.sessions", self._sessions_payload())

    def _list_sessions(self, params, respond):
        respond(self._sessions_payload())

    def _record(self):
        return self._taker.record()  # LIFT: match the old record closure exactly

    def _hotkey_name(self):
        # LIFT: copy the old file's hotkey_name expression verbatim
        raise NotImplementedError("replace with the old file's hotkey_name computation")

    def _start_session(self, params, respond):
        name = str(params.get("name", ""))
        session = next(
            (s for s in training_content.SESSIONS if s["name"] == name), None
        )
        if session is None or self._busy or self._taker is None:
            respond(error="cannot start session")
            return
        self._busy = True
        ui = _WebTrainingUI(self)
        thread = threading.Thread(
            target=training._run_session,
            args=(session, self.profile, self._record, self._hotkey_name(), ui),
            daemon=True,
        )
        thread.start()
        respond(True)

    def _practice(self, params, respond):
        term = str(params.get("text", "")).strip()
        if term == "" or self._busy or self._taker is None:
            respond(error="cannot start practice")
            return
        self._busy = True
        self.profile.add_word(term)
        ui = _WebTrainingUI(self)
        thread = threading.Thread(
            target=training._train_prompt,
            args=(term, [term], self.profile, self._record, self._hotkey_name(), ui),
            daemon=True,
        )
        thread.start()
        respond(True)
```

**Implementer contract for the LIFT markers:** every `# LIFT` line must be replaced with the old file's exact code/strings (Step 1's notes). The `_hotkey_name` placeholder MUST be implemented — leaving the `NotImplementedError` is a task failure. If the old `_WindowUI` used different feedback wording than the placeholders above, the old wording wins. If `TrainingUI` has methods not covered here, add relays for them the same way.

- [ ] **Step 3: Frontend follow-up — `training.sessions` event**

Task 4's page calls `training.listSessions` on mount but should also listen for pushes. In `frontend/src/training/App.tsx`, inside the embedded `useEffect`, add alongside the other listeners:

```tsx
const offSessions = bridge.on('training.sessions', (payload) => {
  const { sessions: s, profileName: p } = payload as { sessions: SessionInfo[]; profileName: string };
  setSessions(s);
  setProfileName(p);
});
```

and add `offSessions();` to the cleanup. Rebuild: `npm run build` (in `frontend/`).

- [ ] **Step 4: Verify**

Run: `.venv/bin/python -m pytest -q` — green.
Run: `grep -rn "TrainingWindowController" speakeasy/ui/` — all call sites use `alloc().initWithEngine_` + `showForProfile_` only.
Run: `grep -n "NotImplementedError" speakeasy/ui/training_window.py` — expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/ui/training_window.py frontend/src/training/App.tsx
git commit -m "Rewrite training window as WKWebView host with practice flow"
```

---

### Task 10: Packaging, pins, docs, final verification

**Files:**
- Modify: `requirements.txt` (one pin)
- Modify: `packaging/Speakeasy.spec` (hiddenimports)
- Modify: `scripts/build_app.sh` (frontend build + bundle + sanity check)
- Modify: `CLAUDE.md` (short web-UI section)
- Modify: `README.md` (build prerequisite note)

- [ ] **Step 1: `requirements.txt`** — add alongside the other pyobjc pins:

```
pyobjc-framework-WebKit==12.2.1
```

Then install into the venv: `.venv/bin/pip install pyobjc-framework-WebKit==12.2.1`

- [ ] **Step 2: `packaging/Speakeasy.spec`** — in `hiddenimports`, add:

```python
"speakeasy.ui.webwindow",
"speakeasy.ui.webbridge",
"WebKit",
```

- [ ] **Step 3: `scripts/build_app.sh`** — immediately after the diarization-models fail-fast check, add:

```bash
# Build the web UI (needs node/npm once at build time — never at runtime).
if ! command -v npm >/dev/null 2>&1; then
    echo "error: npm not found — install Node.js; it is needed at build time to build the UI"
    exit 1
fi
echo "Building frontend…"
npm --prefix frontend ci
npm --prefix frontend run build
if [ ! -f frontend/dist/dock.html ]; then
    echo "error: frontend build produced no dist/dock.html"
    exit 1
fi
```

After the existing model/diarization `rsync` lines, add:

```bash
rsync -a --delete frontend/dist/ "$APP/Contents/Resources/frontend/"
```

In the sanity-check section, add (matching the existing style):

```bash
[ -f "$APP/Contents/Resources/frontend/dock.html" ] || { echo "error: frontend missing from bundle"; exit 1; }
```

- [ ] **Step 4: Docs**

`CLAUDE.md` — add a short bullet under Conventions:

```markdown
- **The three windows are web-rendered** (`ui/webwindow.py` hosts the built
  `frontend/` pages in transparent WKWebViews over the usual glass windows;
  `ui/webbridge.py` is the pure-logic bridge half, tested without WebKit).
  All webview calls are main-thread only — engine callbacks keep hopping via
  `performSelectorOnMainThread`, then `evaluateJavaScript`. Building the app
  (and running from source) needs `npm --prefix frontend run build` first;
  node/npm is a build-time-only dependency, nothing web ships beyond the
  static files in `Resources/frontend/`.
```

`README.md` — in the build section, note Node.js/npm as a one-time build prerequisite and that `build_app.sh` builds the UI automatically.

- [ ] **Step 5: Full verification**

```bash
.venv/bin/python -m pytest -q                 # all green
npm --prefix frontend run build                # exit 0
.venv/bin/python -m speakeasy                  # manual smoke, see below
```

Manual smoke (from source): dock window opens with real translucency (desktop visible through it), native traffic lights, status shows real profile name; Begin Meeting flips to recording state with ticking timer; End Meeting → progress text → transcript appears in Meetings window (open via dock button); Copy/Export/Rename/Delete work; Training opens, session list reflects the profile, practicing a custom word round-trips (hold hotkey, speak, feedback line appears). Quit link quits.

Then bundle: `scripts/build_app.sh` — build completes, sanity checks pass. (Do NOT run `--install` unattended; leave installing to the user so permission grants aren't disturbed without them present.)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt packaging/Speakeasy.spec scripts/build_app.sh CLAUDE.md README.md
git commit -m "Package web frontend into the app bundle"
```
