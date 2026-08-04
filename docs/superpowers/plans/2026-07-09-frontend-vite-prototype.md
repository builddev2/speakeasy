# Speakeasy Frontend Prototype (React/Vite/TS) Implementation Plan

> **Historical implementation record — completed.** This plan preserves the
> phase-1 browser-prototype snapshot and its planning-time checkboxes/code.
> The current frontend is embedded in WKWebViews, uses the real Python bridge,
> and builds with Vite 6.4.3; use `README.md`, `AGENTS.md`, `frontend/`, and
> `speakeasy/ui/` as the current sources of truth.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone React + Vite + TypeScript prototype of Speakeasy's 3 windows (dock panel, meetings, training) that pixel-matches the design reference, running in a browser with mock data — no Python/PyObjC changes.

**Architecture:** A Vite multi-page app under `frontend/` with three independent HTML entries (`dock.html`, `meetings.html`, `training.html`), each mounting its own React root. A shared component library (`src/components/`) and a global design-tokens stylesheet (`src/styles/tokens.css`) back all three. Mock data lives in `src/mock/meetings.ts`.

**Tech Stack:** React 18, TypeScript 5, Vite 5, plain CSS Modules (no Tailwind, no component libraries), npm.

**Spec:** `docs/superpowers/specs/2026-07-09-frontend-vite-prototype-design.md`

## Global Constraints

- Plain CSS only — CSS Modules per component (`*.module.css`) + one global `styles/tokens.css`. No Tailwind, no component libraries.
- Vite **multi-page** build (`build.rollupOptions.input` = the 4 HTML entries), not a single-page app with a router — each window is a separate native window in a later phase.
- No backend/engine integration, no automated test suite (per spec's non-goals). Every task verifies with `npm run build` (`tsc --noEmit && vite build`), which catches type errors and JSX mistakes, plus targeted `grep` against the built CSS for literal values transcribed from the spec.
- All colors, radii, blur amounts, font stacks, and animation timings must match `~/Downloads/speakeasy-reference.html` and the artifact bundle's literal values exactly — these are given verbatim in each task below, do not approximate.
- `@keyframes pill` (used by `Waveform` via **inline style**, so its name cannot be hashed by CSS Modules) must live in the **global** `styles/tokens.css`, not a `*.module.css` file. `bloom` and `recdot` must do the *opposite* — be declared **locally**, inside `Waveform.module.css` and `StatusDot.module.css` respectively, in the same file that references them via a static `animation:` rule. This was verified empirically: Vite's CSS Modules hashes any identifier in an `animation`/`animation-name` property regardless of whether a matching local `@keyframes` exists in that file, so a module rule referencing an *external* global keyframe silently breaks (the reference gets hashed to a name with no matching definition) — the fix is for the declaration and the reference to live in the same file, not to make the keyframe global.
- Working directory for every command below: `frontend/` (create it at the repo root, sibling to `speakeasy/`).

---

### Task 1: Scaffold the Vite multi-page project

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/vite-env.d.ts`
- Create: `frontend/index.html`
- Create: `frontend/dock.html`
- Create: `frontend/meetings.html`
- Create: `frontend/training.html`
- Create: `frontend/src/dock/main.tsx`
- Create: `frontend/src/dock/App.tsx`
- Create: `frontend/src/meetings/main.tsx`
- Create: `frontend/src/meetings/App.tsx`
- Create: `frontend/src/training/main.tsx`
- Create: `frontend/src/training/App.tsx`

**Interfaces:**
- Produces: `DockApp` (default export removed — named export) from `src/dock/App.tsx`, `MeetingsApp` from `src/meetings/App.tsx`, `TrainingApp` from `src/training/App.tsx`. Each is a zero-arg-callable React function component for now (placeholder body); later tasks add props.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "speakeasy-frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/node": "^20.14.0",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.3",
    "vite": "^5.4.1"
  }
}
```

`@types/node` is required because `vite.config.ts` imports `node:url` (Step 4 below) and `tsconfig.json`'s `include` covers `vite.config.ts`, so `tsc --noEmit` type-checks it — without Node's type declarations that import fails to resolve even though it works fine at runtime. This is a dev-time-only type-checking dependency; it has no effect on the shipped bundle.

- [ ] **Step 2: Create `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src", "vite.config.ts"]
}
```

- [ ] **Step 3: Create `frontend/src/vite-env.d.ts`**

```ts
/// <reference types="vite/client" />
```

This is required for TypeScript to recognize `*.module.css` imports and `import.meta.url` — omitting it causes `tsc` to fail on every component that imports a CSS module.

- [ ] **Step 4: Create `frontend/vite.config.ts`**

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./index.html', import.meta.url)),
        dock: fileURLToPath(new URL('./dock.html', import.meta.url)),
        meetings: fileURLToPath(new URL('./meetings.html', import.meta.url)),
        training: fileURLToPath(new URL('./training.html', import.meta.url)),
      },
    },
  },
});
```

- [ ] **Step 5: Create `frontend/index.html`** (dev-only picker, plain unstyled)

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Speakeasy — dev picker</title>
  </head>
  <body>
    <h1>Speakeasy — dev picker</h1>
    <ul>
      <li><a href="/dock.html">Dock panel</a></li>
      <li><a href="/meetings.html">Meetings</a></li>
      <li><a href="/training.html">Training</a></li>
    </ul>
  </body>
</html>
```

- [ ] **Step 6: Create `frontend/dock.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Speakeasy — Dock</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/dock/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: Create `frontend/meetings.html`** (same shape as Step 6, `training` → `meetings`)

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Speakeasy — Meetings</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/meetings/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: Create `frontend/training.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Speakeasy — Training</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/training/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 9: Create placeholder `frontend/src/dock/App.tsx`**

```tsx
export function DockApp() {
  return <div>Dock — placeholder</div>;
}
```

- [ ] **Step 10: Create `frontend/src/dock/main.tsx`**

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { DockApp } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DockApp />
  </StrictMode>,
);
```

- [ ] **Step 11: Create placeholder `frontend/src/meetings/App.tsx`**

```tsx
export function MeetingsApp() {
  return <div>Meetings — placeholder</div>;
}
```

- [ ] **Step 12: Create `frontend/src/meetings/main.tsx`** (same pattern as Step 10, importing `MeetingsApp` from `./App`)

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MeetingsApp } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MeetingsApp />
  </StrictMode>,
);
```

- [ ] **Step 13: Create placeholder `frontend/src/training/App.tsx`**

```tsx
export function TrainingApp() {
  return <div>Training — placeholder</div>;
}
```

- [ ] **Step 14: Create `frontend/src/training/main.tsx`**

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { TrainingApp } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TrainingApp />
  </StrictMode>,
);
```

- [ ] **Step 15: Install dependencies and verify the build**

Run: `cd frontend && npm install && npm run build`
Expected: exits 0, no TypeScript errors.

Run: `ls dist/*.html`
Expected: `dist/dock.html`, `dist/index.html`, `dist/meetings.html`, `dist/training.html` all present.

- [ ] **Step 16: Commit**

```bash
git add frontend/
git commit -m "Scaffold Vite multi-page React/TS frontend"
```

---

### Task 2: Design tokens and desktop backdrop

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/dock/main.tsx` (add import)
- Modify: `frontend/src/meetings/main.tsx` (add import)
- Modify: `frontend/src/training/main.tsx` (add import)

**Interfaces:**
- Produces: global CSS custom properties (`--coral-1`, `--coral-2`, `--red-1`, `--red-2`, `--green`, `--rec-red`, `--amber`, `--text-hi`, `--text`, `--text-mid`, `--text-lo`, `--text-xlo`, `--glass-panel`, `--glass-fill`, `--glass-fill-2`, `--hairline`, `--hairline-lo`, `--sp-1`..`--sp-7`, `--sans`, `--mono`, `--shadow-panel`, `--radius`) and a global `@keyframes pill`, usable by literal name (unhashed) from `Waveform`'s inline styles. `bloom` and `recdot` are NOT defined here — they're declared locally in Tasks 4 and 6, in the same files that use them.

- [ ] **Step 1: Create `frontend/src/styles/tokens.css`**

```css
:root {
  /* Brand */
  --coral-1: #E8955A;
  --coral-2: #D97844;
  --red-1:   #FF5A4E;
  --red-2:   #E23B32;

  /* Semantic status */
  --green:   #3DDC84;
  --rec-red: #FF453A;
  --amber:   #F0B34A;

  /* Neutrals / text on dark glass */
  --text-hi: rgba(255,255,255,0.97);
  --text:    rgba(255,255,255,0.88);
  --text-mid:rgba(255,255,255,0.62);
  --text-lo: rgba(255,255,255,0.40);
  --text-xlo:rgba(255,255,255,0.32);

  /* Glass surfaces */
  --glass-panel: linear-gradient(180deg, rgba(42,46,48,0.52), rgba(22,25,26,0.55));
  --glass-fill:  rgba(255,255,255,0.08);
  --glass-fill-2:rgba(255,255,255,0.05);
  --hairline:    rgba(255,255,255,0.16);
  --hairline-lo: rgba(255,255,255,0.07);

  /* Speaker palette (transcript color-coding) */
  --sp-1:#6fd8b0; --sp-2:#F0B34A; --sp-3:#C08CF2; --sp-4:#6EA8FF;
  --sp-5:#7ED97E; --sp-6:#F0895A; --sp-7:#DE8FB4;

  /* Type */
  --sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif;
  --mono: ui-monospace, "SF Mono", "SFMono-Regular", Menlo, monospace;

  /* Elevation */
  --shadow-panel: inset 0 1px 0 rgba(255,255,255,0.14),
                  0 24px 60px -18px rgba(0,0,0,0.65),
                  0 3px 12px rgba(0,0,0,0.4);
  --radius: 16px;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  min-height: 100vh;
}

body {
  font-family: var(--sans);
  color: var(--text);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  background: #0c1310;
}

body::before {
  content: '';
  position: fixed;
  inset: -10%;
  z-index: 0;
  filter: saturate(1.05);
  background:
    radial-gradient(closest-side at 22% 26%, rgba(232,149,90,0.55), transparent 70%),
    radial-gradient(closest-side at 82% 18%, rgba(86,150,255,0.45), transparent 70%),
    radial-gradient(closest-side at 68% 82%, rgba(191,90,242,0.42), transparent 70%),
    radial-gradient(closest-side at 14% 88%, rgba(48,209,120,0.40), transparent 70%),
    linear-gradient(160deg, #16241d, #0c1310 70%);
}

#root {
  position: relative;
  z-index: 1;
}

@keyframes pill { 0%,100% { transform: scaleY(0.24); } 50% { transform: scaleY(1); } }
```

- [ ] **Step 2: Import it in all three entry points**

In `frontend/src/dock/main.tsx`, `frontend/src/meetings/main.tsx`, and `frontend/src/training/main.tsx`, add as the first import:

```tsx
import '../styles/tokens.css';
```

(e.g. `frontend/src/dock/main.tsx` becomes:)

```tsx
import '../styles/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { DockApp } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DockApp />
  </StrictMode>,
);
```

- [ ] **Step 3: Build and verify tokens made it into the output**

Run: `npm run build`
Expected: exits 0.

Run: `grep -rl "coral-1" dist/assets/*.css`
Expected: at least one matching file (confirms the custom properties compiled in, unhashed since they're global, not module-scoped).

Run: `grep -rl "@keyframes pill" dist/assets/*.css`
Expected: at least one matching file, and the keyframes name is literally `pill` (not hashed) — confirms it stayed global.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/styles frontend/src/dock/main.tsx frontend/src/meetings/main.tsx frontend/src/training/main.tsx
git commit -m "Add design tokens and desktop backdrop"
```

---

### Task 3: GlassPanel and TitleBar components

**Files:**
- Create: `frontend/src/components/GlassPanel.tsx`
- Create: `frontend/src/components/GlassPanel.module.css`
- Create: `frontend/src/components/TitleBar.tsx`
- Create: `frontend/src/components/TitleBar.module.css`

**Interfaces:**
- Consumes: nothing (leaf components).
- Produces:
  - `GlassPanel(props: { width: number; height: number; children: ReactNode })` — default export removed, named export `GlassPanel`.
  - `TitleBar(props: { plain?: boolean; title?: ReactNode })` — named export `TitleBar`. `plain` defaults to `false` (34px bar with bottom hairline and 3 traffic lights); when `true`, renders a 32px bar with no border and no title. `title` renders after the lights when provided.

- [ ] **Step 1: Create `frontend/src/components/GlassPanel.module.css`**

```css
.panel {
  position: relative;
  border-radius: 16px;
  overflow: hidden;
  background: linear-gradient(180deg, rgba(42,46,48,0.52), rgba(22,25,26,0.55));
  backdrop-filter: blur(40px) saturate(180%);
  -webkit-backdrop-filter: blur(40px) saturate(180%);
  border: 0.5px solid rgba(255,255,255,0.16);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.14),
              0 24px 60px -18px rgba(0,0,0,0.65),
              0 3px 12px rgba(0,0,0,0.4);
}
```

- [ ] **Step 2: Create `frontend/src/components/GlassPanel.tsx`**

```tsx
import type { CSSProperties, ReactNode } from 'react';
import styles from './GlassPanel.module.css';

interface GlassPanelProps {
  width: number;
  height: number;
  children: ReactNode;
}

export function GlassPanel({ width, height, children }: GlassPanelProps) {
  const style: CSSProperties = { width, height };
  return (
    <div className={styles.panel} style={style}>
      {children}
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/TitleBar.module.css`**

```css
.titlebar {
  height: 34px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  border-bottom: 0.5px solid rgba(255,255,255,0.07);
}

.plain {
  height: 32px;
  border-bottom: none;
}

.light {
  width: 11px;
  height: 11px;
  border-radius: 50%;
}

.red { background: #ff5f57; }
.yellow { background: #febc2e; }
.green { background: #28c840; }

.title {
  margin-left: 8px;
  font: 600 13px/1 var(--sans);
  color: rgba(255,255,255,0.72);
}
```

- [ ] **Step 4: Create `frontend/src/components/TitleBar.tsx`**

```tsx
import type { ReactNode } from 'react';
import styles from './TitleBar.module.css';

interface TitleBarProps {
  plain?: boolean;
  title?: ReactNode;
}

export function TitleBar({ plain = false, title }: TitleBarProps) {
  return (
    <div className={plain ? `${styles.titlebar} ${styles.plain}` : styles.titlebar}>
      <span className={`${styles.light} ${styles.red}`} />
      <span className={`${styles.light} ${styles.yellow}`} />
      <span className={`${styles.light} ${styles.green}`} />
      {title && <span className={styles.title}>{title}</span>}
    </div>
  );
}
```

- [ ] **Step 5: Wire into the three placeholder Apps to confirm they render**

Replace the body of `frontend/src/dock/App.tsx` with:

```tsx
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';

export function DockApp() {
  return (
    <GlassPanel width={360} height={300}>
      <TitleBar plain />
      <div>Dock — placeholder</div>
    </GlassPanel>
  );
}
```

Replace the body of `frontend/src/meetings/App.tsx` with:

```tsx
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';

export function MeetingsApp() {
  return (
    <GlassPanel width={720} height={480}>
      <TitleBar title="Meetings" />
      <div>Meetings — placeholder</div>
    </GlassPanel>
  );
}
```

Replace the body of `frontend/src/training/App.tsx` with:

```tsx
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';

export function TrainingApp() {
  return (
    <GlassPanel width={640} height={440}>
      <TitleBar title="Training" />
      <div>Training — placeholder</div>
    </GlassPanel>
  );
}
```

- [ ] **Step 6: Build and verify**

Run: `npm run build`
Expected: exits 0.

Run: `grep -rl "backdrop-filter:blur(40px) saturate(180%)" dist/assets/*.css`
Expected: at least one matching file.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components frontend/src/dock/App.tsx frontend/src/meetings/App.tsx frontend/src/training/App.tsx
git commit -m "Add GlassPanel and TitleBar shared components"
```

---

### Task 4: AppIdentity and StatusDot components

**Files:**
- Create: `frontend/src/components/StatusDot.tsx`
- Create: `frontend/src/components/StatusDot.module.css`
- Create: `frontend/src/components/AppIdentity.tsx`
- Create: `frontend/src/components/AppIdentity.module.css`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `StatusDot(props: { variant: 'green' | 'rec' })` — named export.
  - `AppIdentity(props: { status: ReactNode; live?: boolean })` — named export. Renders the skull icon + "Speakeasy" wordmark + a status-line slot; `live` (default `false`) bumps the status line to bold/bright (used while recording).

- [ ] **Step 1: Create `frontend/src/components/StatusDot.module.css`**

```css
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.green {
  background: #3ddc84;
  box-shadow: 0 0 7px 1px rgba(61,220,132,0.7);
}

.rec {
  background: #ff453a;
  box-shadow: 0 0 8px 1px rgba(255,69,58,0.7);
  animation: recdot 1.4s ease-in-out infinite;
}

@keyframes recdot { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
```

`recdot` is declared locally in this file (not in `tokens.css`) because it's only referenced by `.rec` in this same file via a static `animation:` rule — CSS Modules hashes both the declaration and the reference together consistently as long as they're co-located. See the Global Constraints note on `pill` vs `bloom`/`recdot` for why.

- [ ] **Step 2: Create `frontend/src/components/StatusDot.tsx`**

```tsx
import styles from './StatusDot.module.css';

interface StatusDotProps {
  variant: 'green' | 'rec';
}

export function StatusDot({ variant }: StatusDotProps) {
  return <span className={`${styles.dot} ${styles[variant]}`} />;
}
```

- [ ] **Step 3: Create `frontend/src/components/AppIdentity.module.css`**

```css
.identity {
  display: flex;
  align-items: center;
  gap: 11px;
}

.icon {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  background: linear-gradient(180deg, rgba(70,72,78,0.85), rgba(40,42,46,0.85));
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.16), 0 3px 8px rgba(0,0,0,0.35);
  filter: saturate(0.2) brightness(1.05);
}

.textBlock {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.wordmark {
  font: 600 19px/1 var(--sans);
  letter-spacing: -0.3px;
  color: rgba(255,255,255,0.97);
}

.status {
  display: flex;
  align-items: center;
  gap: 6px;
  font: 400 13px/1 var(--sans);
  color: rgba(255,255,255,0.55);
}

.statusLive {
  font-weight: 600;
  color: rgba(255,255,255,0.9);
}
```

- [ ] **Step 4: Create `frontend/src/components/AppIdentity.tsx`**

```tsx
import type { ReactNode } from 'react';
import styles from './AppIdentity.module.css';

interface AppIdentityProps {
  status: ReactNode;
  live?: boolean;
}

export function AppIdentity({ status, live = false }: AppIdentityProps) {
  return (
    <div className={styles.identity}>
      <div className={styles.icon}>💀</div>
      <div className={styles.textBlock}>
        <div className={styles.wordmark}>Speakeasy</div>
        <div className={live ? `${styles.status} ${styles.statusLive}` : styles.status}>{status}</div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Wire a preview into `frontend/src/dock/App.tsx`** to confirm it renders (final wiring happens in Task 8)

```tsx
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { AppIdentity } from '../components/AppIdentity';
import { StatusDot } from '../components/StatusDot';

export function DockApp() {
  return (
    <GlassPanel width={360} height={300}>
      <TitleBar plain />
      <AppIdentity status={<><StatusDot variant="green" /> Ready — Jason</>} />
    </GlassPanel>
  );
}
```

- [ ] **Step 6: Build and verify**

Run: `npm run build`
Expected: exits 0.

Run: `grep -rl "1.4s ease-in-out infinite" dist/assets/*.css`
Expected: at least one matching file. (Checks the timing values rather than the `recdot` identifier itself, since CSS Modules hashes that name in the compiled output — the hash is consistent between the local `@keyframes recdot` and its `.rec` reference, but not predictable ahead of time.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/StatusDot.tsx frontend/src/components/StatusDot.module.css frontend/src/components/AppIdentity.tsx frontend/src/components/AppIdentity.module.css frontend/src/dock/App.tsx
git commit -m "Add AppIdentity and StatusDot components"
```

---

### Task 5: Button components

**Files:**
- Create: `frontend/src/components/PrimaryButton.tsx`
- Create: `frontend/src/components/PrimaryButton.module.css`
- Create: `frontend/src/components/GlassButton.tsx`
- Create: `frontend/src/components/GlassButton.module.css`
- Create: `frontend/src/components/ActionButton.tsx`
- Create: `frontend/src/components/ActionButton.module.css`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PrimaryButton(props: { children: ReactNode; danger?: boolean; className?: string; onClick?: () => void })` — named export. `danger` (default `false`) swaps the coral gradient for the red one. `className` merges in caller-supplied classes (used for per-page margin overrides).
  - `GlassButton(props: { icon: ReactNode; label: string; dim?: boolean })` — named export. `dim` (default `false`) shrinks it to the disabled/dimmed visual used while a meeting is recording.
  - `ActionButton(props: { children: ReactNode; variant?: 'default' | 'strong' | 'danger'; className?: string; onClick?: () => void })` — named export. `variant` defaults to `'default'`.

- [ ] **Step 1: Create `frontend/src/components/PrimaryButton.module.css`**

```css
.btn {
  width: 100%;
  height: 46px;
  border-radius: 12px;
  border: none;
  cursor: pointer;
  font: 600 16px/1 var(--sans);
  letter-spacing: .1px;
  color: #fff;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  background: linear-gradient(180deg, rgba(235,152,93,0.92), rgba(214,120,68,0.80));
  border: 0.5px solid rgba(255,255,255,0.35);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4), 0 8px 22px -8px rgba(217,122,69,0.7), 0 2px 6px rgba(0,0,0,0.3);
}

.danger {
  background: linear-gradient(180deg, rgba(255,90,78,0.92), rgba(226,59,50,0.80));
  border-color: rgba(255,255,255,0.32);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.35), 0 8px 22px -8px rgba(226,59,50,0.65), 0 2px 6px rgba(0,0,0,0.3);
}
```

- [ ] **Step 2: Create `frontend/src/components/PrimaryButton.tsx`**

```tsx
import type { ReactNode } from 'react';
import styles from './PrimaryButton.module.css';

interface PrimaryButtonProps {
  children: ReactNode;
  danger?: boolean;
  className?: string;
  onClick?: () => void;
}

export function PrimaryButton({ children, danger = false, className, onClick }: PrimaryButtonProps) {
  const classes = [styles.btn, danger ? styles.danger : '', className].filter(Boolean).join(' ');
  return (
    <button className={classes} onClick={onClick}>
      {children}
    </button>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/GlassButton.module.css`**

```css
.btn {
  flex: 1;
  height: 46px;
  border-radius: 11px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  gap: 9px;
  border: none;
  cursor: pointer;
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  background: rgba(255,255,255,0.08);
  border: 0.5px solid rgba(255,255,255,0.14);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
  font: 600 14px/1 var(--sans);
  color: rgba(255,255,255,0.88);
  white-space: nowrap;
}

.dim {
  height: 42px;
  background: rgba(255,255,255,0.05);
  border-color: rgba(255,255,255,0.1);
  font-size: 13px;
}
```

- [ ] **Step 4: Create `frontend/src/components/GlassButton.tsx`**

```tsx
import type { ReactNode } from 'react';
import styles from './GlassButton.module.css';

interface GlassButtonProps {
  icon: ReactNode;
  label: string;
  dim?: boolean;
}

export function GlassButton({ icon, label, dim = false }: GlassButtonProps) {
  return (
    <button className={dim ? `${styles.btn} ${styles.dim}` : styles.btn}>
      {icon}
      <span>{label}</span>
    </button>
  );
}
```

- [ ] **Step 5: Create `frontend/src/components/ActionButton.module.css`**

```css
.btn {
  height: 34px;
  padding: 0 16px;
  border-radius: 9px;
  border: none;
  cursor: pointer;
  font: 600 13px/1 var(--sans);
  color: rgba(255,255,255,0.92);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  background: rgba(255,255,255,0.08);
  border: 0.5px solid rgba(255,255,255,0.14);
}

.strong {
  background: rgba(255,255,255,0.10);
  border-color: rgba(255,255,255,0.16);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.1);
}

.danger {
  color: #FF8A80;
  background: rgba(255,90,78,0.14);
  border-color: rgba(255,90,78,0.30);
}
```

- [ ] **Step 6: Create `frontend/src/components/ActionButton.tsx`**

```tsx
import type { ReactNode } from 'react';
import styles from './ActionButton.module.css';

interface ActionButtonProps {
  children: ReactNode;
  variant?: 'default' | 'strong' | 'danger';
  className?: string;
  onClick?: () => void;
}

export function ActionButton({ children, variant = 'default', className, onClick }: ActionButtonProps) {
  const variantClass = variant !== 'default' ? styles[variant] : '';
  const classes = [styles.btn, variantClass, className].filter(Boolean).join(' ');
  return (
    <button className={classes} onClick={onClick}>
      {children}
    </button>
  );
}
```

- [ ] **Step 7: Verify**

Run: `npx tsc --noEmit`
Expected: exits 0, no errors. (These components aren't imported by any App yet — Tasks 8–9 wire them in — but `tsconfig.json`'s `include: ["src", ...]` type-checks the whole directory regardless of import graph, so this still catches mistakes now.)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/PrimaryButton.tsx frontend/src/components/PrimaryButton.module.css frontend/src/components/GlassButton.tsx frontend/src/components/GlassButton.module.css frontend/src/components/ActionButton.tsx frontend/src/components/ActionButton.module.css
git commit -m "Add PrimaryButton, GlassButton, and ActionButton components"
```

---

### Task 6: Waveform component

**Files:**
- Create: `frontend/src/components/Waveform.tsx`
- Create: `frontend/src/components/Waveform.module.css`

**Interfaces:**
- Consumes: nothing.
- Produces: `Waveform()` — named export, no props. Renders the 26-bar rainbow hero used only in the dock's recording state.

- [ ] **Step 1: Create `frontend/src/components/Waveform.module.css`**

```css
.hero {
  position: relative;
  margin-top: 14px;
  height: 52px;
  border-radius: 12px;
  overflow: hidden;
  background: linear-gradient(180deg, rgba(0,0,0,0.28), rgba(0,0,0,0.14));
  border: 0.5px solid rgba(255,255,255,0.08);
}

.bloom {
  position: absolute;
  inset: 0;
  filter: blur(20px);
  animation: bloom 3s ease-in-out infinite;
  background: linear-gradient(90deg, rgba(255,59,48,0.5), rgba(255,159,10,0.5), rgba(255,214,10,0.45),
              rgba(48,209,88,0.45), rgba(10,132,255,0.5), rgba(191,90,242,0.5));
}

.bars {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  padding: 12px 16px;
}

.bar {
  display: block;
  width: 4px;
  height: 100%;
  border-radius: 2.5px;
  transform-origin: bottom;
}

@keyframes bloom { 0%,100% { opacity: 0.5; } 50% { opacity: 0.85; } }
```

`bloom` is declared locally in this file (not in `tokens.css`) because it's only referenced by `.bloom` in this same file via a static `animation:` rule — CSS Modules hashes both the declaration and the reference together consistently as long as they're co-located (verified empirically; see the Global Constraints note). `pill`, below, is the opposite case — it's set via inline style from JS, which CSS Modules never touches, so it must stay in the global `tokens.css` with its literal name.

- [ ] **Step 2: Create `frontend/src/components/Waveform.tsx`**

```tsx
import styles from './Waveform.module.css';

const BAR_COUNT = 26;

interface Bar {
  key: number;
  style: {
    background: string;
    boxShadow: string;
    animation: string;
  };
}

function buildBars(): Bar[] {
  return Array.from({ length: BAR_COUNT }, (_, i) => {
    const hue = Math.round((i * 300) / (BAR_COUNT - 1));
    const duration = (0.9 + (i % 4) * 0.13).toFixed(2);
    const delay = ((i % 6) * 0.1 + i * 0.014).toFixed(2);
    return {
      key: i,
      style: {
        background: `linear-gradient(180deg, rgba(255,255,255,0.85), hsl(${hue} 92% 62%) 42%, hsl(${hue} 88% 48%))`,
        boxShadow: `0 0 7px 0 hsl(${hue} 95% 60% / 0.75), inset 0 0 2px 0 rgba(255,255,255,0.6)`,
        animation: `pill ${duration}s ${delay}s ease-in-out infinite`,
      },
    };
  });
}

export function Waveform() {
  const bars = buildBars();
  return (
    <div className={styles.hero}>
      <div className={styles.bloom} />
      <div className={styles.bars}>
        {bars.map((bar) => (
          <span key={bar.key} className={styles.bar} style={bar.style} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Build and verify**

Run: `npx tsc --noEmit`
Expected: exits 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Waveform.tsx frontend/src/components/Waveform.module.css
git commit -m "Add Waveform component"
```

---

### Task 7: Mock meeting data

**Files:**
- Create: `frontend/src/mock/meetings.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `interface TranscriptLine { time: string; speaker: string; text: string }`
  - `interface Meeting { id: string; title: string; subtitle: string; date: string; duration: string; speakerCount: number; lines: TranscriptLine[] }`
  - `SPEAKER_PALETTE: string[]` (7 hex colors)
  - `speakerColor(speakerNumber: number, colorCodeSpeakers: boolean): string`
  - `MEETINGS: Meeting[]` (3 seed meetings, first has 10 lines matching the reference verbatim, other two have 2–3 invented lines each)

- [ ] **Step 1: Create `frontend/src/mock/meetings.ts`**

```ts
export interface TranscriptLine {
  time: string;
  speaker: string;
  text: string;
}

export interface Meeting {
  id: string;
  title: string;
  subtitle: string;
  date: string;
  duration: string;
  speakerCount: number;
  lines: TranscriptLine[];
}

export const SPEAKER_PALETTE = [
  '#6fd8b0',
  '#F0B34A',
  '#C08CF2',
  '#6EA8FF',
  '#7ED97E',
  '#F0895A',
  '#DE8FB4',
];

export function speakerColor(speakerNumber: number, colorCodeSpeakers: boolean): string {
  if (!colorCodeSpeakers) return 'rgba(255,255,255,0.55)';
  return SPEAKER_PALETTE[(speakerNumber - 1) % SPEAKER_PALETTE.length];
}

function line(time: string, speakerNumber: number, text: string): TranscriptLine {
  return { time: `[${time}]`, speaker: `Speaker ${speakerNumber}:`, text };
}

export const MEETINGS: Meeting[] = [
  {
    id: 'meeting-1',
    title: 'Meeting — Jul 8, 7:43 PM',
    subtitle: '5 min · 17 speakers',
    date: 'Jul 8, 2026 · 19:43',
    duration: '5 min',
    speakerCount: 17,
    lines: [
      line('00:00:00', 1, "Okay, are we recording? Yep — we're live."),
      line('00:00:15', 2, "Great. Let's start with last week's numbers."),
      line('00:00:27', 3, "Revenue's up nine percent over the quarter."),
      line('00:00:36', 4, 'When you finish, walk me through the churn cohort.'),
      line('00:00:42', 5, 'Sure — give me one second to pull it up.'),
      line('00:00:55', 6, 'While that loads — any blockers on the mobile build?'),
      line('00:01:03', 1, 'One. Offline sync still drops on airplane mode.'),
      line('00:01:10', 2, "Let's file that as a P1 and keep moving."),
      line('00:01:21', 7, "Agreed — I'll take it right after standup."),
      line('00:02:31', 3, "Circling back — the cohort's holding at ninety-four percent."),
    ],
  },
  {
    id: 'meeting-2',
    title: 'Test Meeting — Jul 8, 6:56 PM',
    subtitle: '2 min · 3 speakers',
    date: 'Jul 8, 2026 · 18:56',
    duration: '2 min',
    speakerCount: 3,
    lines: [
      line('00:00:00', 1, 'Quick sync — can everyone hear me okay?'),
      line('00:00:08', 2, 'Loud and clear.'),
      line('00:00:14', 3, "Same here, let's keep this short."),
    ],
  },
  {
    id: 'meeting-3',
    title: 'Test Meeting — Jul 8, 6:53 PM',
    subtitle: '1 min · 2 speakers',
    date: 'Jul 8, 2026 · 18:53',
    duration: '1 min',
    speakerCount: 2,
    lines: [
      line('00:00:00', 1, 'Testing the recorder before the real call.'),
      line('00:00:06', 2, 'Sounds good on my end.'),
    ],
  },
];
```

- [ ] **Step 2: Verify**

Run: `npx tsc --noEmit`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/mock/meetings.ts
git commit -m "Add mock meeting/transcript data"
```

---

### Task 8: Dock window — full implementation

**Files:**
- Modify: `frontend/src/dock/App.tsx`
- Create: `frontend/src/dock/App.module.css`

**Interfaces:**
- Consumes: `GlassPanel` (Task 3), `TitleBar` (Task 3), `AppIdentity`, `StatusDot` (Task 4), `PrimaryButton`, `GlassButton` (Task 5), `Waveform` (Task 6) — all exact signatures as defined above.
- Produces: `DockApp(props: { operatorName?: string; readyMessage?: string })` — named export. Defaults: `operatorName = 'Jason'`, `readyMessage = 'Ready'`.

- [ ] **Step 1: Create `frontend/src/dock/App.module.css`**

```css
.body {
  padding: 8px 18px 16px;
  height: calc(100% - 32px);
  display: flex;
  flex-direction: column;
}

.primaryIdle {
  margin-top: 20px;
}

.primaryRecording {
  margin-top: 12px;
}

.row {
  display: flex;
  gap: 10px;
  margin-top: 10px;
}

.rowDisabled {
  opacity: 0.4;
  pointer-events: none;
}

.foot {
  margin-top: auto;
  padding-top: 14px;
  display: flex;
  justify-content: flex-end;
}

.quit {
  font: 400 12.5px/1 var(--sans);
  color: rgba(255,255,255,0.32);
  text-decoration: none;
}

.quit:hover {
  color: rgba(255,255,255,0.5);
}

.name {
  color: rgba(255,255,255,0.9);
  font-weight: 600;
}

.recStatus {
  margin-top: 9px;
}

.pillBadge {
  font: 500 11.5px/1 var(--sans);
  color: #F0B34A;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(240,179,74,0.13);
  border: 0.5px solid rgba(240,179,74,0.25);
}

.timer {
  font: 600 12.5px/1 var(--mono);
  color: rgba(255,255,255,0.7);
  letter-spacing: .4px;
}
```

- [ ] **Step 2: Replace `frontend/src/dock/App.tsx` with the full implementation**

```tsx
import { useEffect, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { AppIdentity } from '../components/AppIdentity';
import { StatusDot } from '../components/StatusDot';
import { PrimaryButton } from '../components/PrimaryButton';
import { GlassButton } from '../components/GlassButton';
import { Waveform } from '../components/Waveform';
import styles from './App.module.css';

interface DockAppProps {
  operatorName?: string;
  readyMessage?: string;
}

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
  const [mode, setMode] = useState<'idle' | 'recording'>('idle');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  function toggleMode() {
    setMode((current) => (current === 'idle' ? 'recording' : 'idle'));
    setElapsedSeconds(0);
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'r' || event.key === 'R') {
        toggleMode();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    if (mode !== 'recording') return;
    const id = window.setInterval(() => {
      setElapsedSeconds((current) => current + 1);
    }, 1000);
    return () => window.clearInterval(id);
  }, [mode]);

  const isRecording = mode === 'recording';

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
                <span className={styles.timer}>· {formatTimer(elapsedSeconds)}</span>
              </>
            ) : (
              <>
                <StatusDot variant="green" /> {readyMessage} —{' '}
                <span className={styles.name}>{operatorName}</span>
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
          onClick={toggleMode}
        >
          {isRecording ? 'End Meeting' : 'Begin Meeting'}
        </PrimaryButton>

        <div className={isRecording ? `${styles.row} ${styles.rowDisabled}` : styles.row}>
          <GlassButton icon={<MeetingsIcon />} label="Meetings" dim={isRecording} />
          <GlassButton icon={<TrainIcon />} label="Train Profile" dim={isRecording} />
        </div>

        <div className={styles.foot}>
          <a className={styles.quit} href="#">Quit Speakeasy</a>
        </div>
      </div>
    </GlassPanel>
  );
}
```

- [ ] **Step 3: Build and verify**

Run: `npm run build`
Expected: exits 0.

Run: `npm run dev` (in background) then open `http://localhost:5173/dock.html` in a browser
Expected: idle state renders — skull icon, "Speakeasy", green dot "Ready — **Jason**", coral "Begin Meeting" button, "Meetings"/"Train Profile" glass buttons, "Quit Speakeasy" link bottom-right. Pressing `R` (or clicking the primary button) switches to the recording state — red pulsing dot, ticking `mm:ss` timer, rainbow waveform, amber "Dictation paused" pill, red "End Meeting" button, dimmed secondary buttons. Pressing `R` again (or clicking End Meeting) returns to idle with the timer reset to `00:00`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/dock/App.tsx frontend/src/dock/App.module.css
git commit -m "Implement dock panel window (idle + recording states)"
```

---

### Task 9: Meetings window — full implementation

**Files:**
- Modify: `frontend/src/meetings/App.tsx`
- Create: `frontend/src/meetings/App.module.css`

**Interfaces:**
- Consumes: `GlassPanel`, `TitleBar` (Task 3), `ActionButton` (Task 5), `MEETINGS`, `speakerColor` (Task 7) — exact signatures as defined above.
- Produces: `MeetingsApp(props: { colorCodeSpeakers?: boolean })` — named export. Default `colorCodeSpeakers = true`.

- [ ] **Step 1: Create `frontend/src/meetings/App.module.css`**

```css
.split {
  display: flex;
  height: calc(100% - 34px);
}

.sidebar {
  width: 232px;
  flex-shrink: 0;
  padding: 12px 8px;
  border-right: 0.5px solid rgba(255,255,255,0.07);
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: rgba(255,255,255,0.02);
  overflow-y: auto;
}

.heading {
  font: 600 11px/1 var(--sans);
  letter-spacing: .08em;
  text-transform: uppercase;
  color: rgba(255,255,255,0.4);
  padding: 4px 10px 8px;
}

.item {
  padding: 9px 11px;
  border-radius: 8px;
  cursor: pointer;
  border: 0.5px solid transparent;
  background: transparent;
  text-align: left;
  width: 100%;
}

.itemActive {
  background: rgba(235,152,93,0.16);
  border-color: rgba(235,152,93,0.28);
}

.itemTitle {
  font: 400 13.5px/1.3 var(--sans);
  color: rgba(255,255,255,0.62);
}

.itemTitleActive {
  color: rgba(255,255,255,0.96);
  font-weight: 600;
}

.itemSub {
  margin-top: 3px;
  font: 400 11.5px/1 var(--sans);
  color: rgba(255,255,255,0.35);
}

.itemSubActive {
  color: rgba(255,255,255,0.5);
}

.detail {
  flex: 1;
  min-width: 0;
  padding: 18px 20px 16px;
  display: flex;
  flex-direction: column;
}

.dTitle {
  font: 600 19px/1.1 var(--sans);
  letter-spacing: -0.3px;
  color: rgba(255,255,255,0.97);
}

.meta {
  margin-top: 7px;
  display: flex;
  align-items: center;
  gap: 9px;
  font: 400 12.5px/1 var(--sans);
  color: rgba(255,255,255,0.62);
}

.sep {
  opacity: 0.4;
}

.transcript {
  margin-top: 14px;
  flex: 1;
  overflow-y: auto;
  position: relative;
  border-radius: 12px;
  background: rgba(0,0,0,0.30);
  border: 0.5px solid rgba(255,255,255,0.08);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}

.lines {
  padding: 14px 16px;
  font: 400 12px/1.7 var(--mono);
}

.fade {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 44px;
  pointer-events: none;
  background: linear-gradient(180deg, rgba(20,22,24,0), rgba(18,20,22,0.75));
}

.time {
  color: rgba(235,152,93,0.72);
}

.speaker {
  font-weight: 600;
}

.text {
  color: rgba(255,255,255,0.82);
}

.actionRow {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 9px;
}

.spacer {
  margin-left: auto;
}
```

- [ ] **Step 2: Replace `frontend/src/meetings/App.tsx` with the full implementation**

```tsx
import { useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { ActionButton } from '../components/ActionButton';
import { MEETINGS, speakerColor } from '../mock/meetings';
import styles from './App.module.css';

interface MeetingsAppProps {
  colorCodeSpeakers?: boolean;
}

export function MeetingsApp({ colorCodeSpeakers = true }: MeetingsAppProps) {
  const [selectedMeetingId, setSelectedMeetingId] = useState(MEETINGS[0].id);
  const meeting = MEETINGS.find((m) => m.id === selectedMeetingId) ?? MEETINGS[0];

  return (
    <GlassPanel width={720} height={480}>
      <TitleBar title="Meetings" />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Meetings</span>
          {MEETINGS.map((m) => {
            const active = m.id === selectedMeetingId;
            return (
              <button
                key={m.id}
                className={active ? `${styles.item} ${styles.itemActive}` : styles.item}
                onClick={() => setSelectedMeetingId(m.id)}
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
          <div className={styles.dTitle}>{meeting.title}</div>
          <div className={styles.meta}>
            <span>{meeting.date}</span>
            <span className={styles.sep}>•</span>
            <span>{meeting.duration}</span>
            <span className={styles.sep}>•</span>
            <span>{meeting.speakerCount} speakers</span>
          </div>
          <div className={styles.transcript}>
            <div className={styles.lines}>
              {meeting.lines.map((ln, index) => {
                const speakerNumber = Number(ln.speaker.replace(/\D/g, ''));
                return (
                  <div key={index}>
                    <span className={styles.time}>{ln.time}</span>{' '}
                    <span className={styles.speaker} style={{ color: speakerColor(speakerNumber, colorCodeSpeakers) }}>
                      {ln.speaker}
                    </span>{' '}
                    <span className={styles.text}>{ln.text}</span>
                  </div>
                );
              })}
            </div>
            <div className={styles.fade} />
          </div>
          <div className={styles.actionRow}>
            <ActionButton variant="strong" onClick={() => console.log('copy')}>Copy</ActionButton>
            <ActionButton onClick={() => console.log('export')}>Export</ActionButton>
            <ActionButton onClick={() => console.log('rename')}>Rename</ActionButton>
            <ActionButton variant="danger" className={styles.spacer} onClick={() => console.log('delete')}>
              Delete
            </ActionButton>
          </div>
        </div>
      </div>
    </GlassPanel>
  );
}
```

- [ ] **Step 3: Build and verify**

Run: `npm run build`
Expected: exits 0.

With `npm run dev` running, open `http://localhost:5173/meetings.html`
Expected: sidebar lists 3 meetings, first one active (coral tint). Clicking a different meeting swaps the detail pane's title/meta/transcript. Transcript lines are mono, speaker labels color-cycle through the 7-color palette. Copy/Export/Rename/Delete buttons are present and styled (Delete right-aligned, red-tinted); clicking any logs to the console and does nothing else.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/meetings/App.tsx frontend/src/meetings/App.module.css
git commit -m "Implement meetings transcript browser window"
```

---

### Task 10: Training window — full implementation

**Files:**
- Modify: `frontend/src/training/App.tsx`
- Create: `frontend/src/training/App.module.css`

**Interfaces:**
- Consumes: `GlassPanel`, `TitleBar` (Task 3) — exact signatures as defined above.
- Produces: `TrainingApp(props: { operatorName?: string })` — named export. Default `operatorName = 'Jason'`.

- [ ] **Step 1: Create `frontend/src/training/App.module.css`**

```css
.split {
  display: flex;
  height: calc(100% - 34px);
}

.sidebar {
  width: 222px;
  flex-shrink: 0;
  padding: 12px 8px;
  border-right: 0.5px solid rgba(255,255,255,0.07);
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: rgba(255,255,255,0.02);
}

.heading {
  font: 600 11px/1 var(--sans);
  letter-spacing: .08em;
  text-transform: uppercase;
  color: rgba(255,255,255,0.4);
  padding: 4px 10px 8px;
}

.item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 11px;
  border-radius: 8px;
}

.itemCurrent {
  background: rgba(255,255,255,0.06);
  border: 0.5px solid rgba(255,255,255,0.1);
}

.name {
  font: 400 13.5px/1 var(--sans);
  color: rgba(255,255,255,0.9);
}

.nameTrunc {
  color: rgba(255,255,255,0.78);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bullet {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: rgba(255,255,255,0.45);
  flex-shrink: 0;
  margin: 0 4.5px;
}

.star {
  margin-left: auto;
  color: #F0B34A;
  font-size: 12px;
}

.detail {
  flex: 1;
  min-width: 0;
  padding: 20px 22px 16px;
  display: flex;
  flex-direction: column;
}

.intro {
  font: 400 13px/1.4 var(--sans);
  color: rgba(255,255,255,0.55);
}

.headline {
  margin: 16px 0 0;
  max-width: 340px;
  font: 600 22px/1.3 var(--sans);
  letter-spacing: -0.4px;
  color: rgba(255,255,255,0.95);
  text-wrap: balance;
}

.inputRow {
  margin-top: auto;
  display: flex;
  gap: 10px;
  align-items: center;
}

.textInput {
  flex: 1;
  height: 42px;
  padding: 0 14px;
  border-radius: 11px;
  background: rgba(0,0,0,0.28);
  border: 1.5px solid rgba(255,255,255,0.14);
  font: 400 14px/1 var(--sans);
  color: rgba(255,255,255,0.9);
  outline: none;
}

.textInput::placeholder {
  color: rgba(255,255,255,0.4);
}

.textInput:focus {
  border-color: #E8955A;
  box-shadow: 0 0 0 3.5px rgba(235,152,93,0.25);
}

.practiceBtn {
  height: 42px;
  padding: 0 22px;
  border-radius: 11px;
  border: none;
  cursor: pointer;
  font: 600 14.5px/1 var(--sans);
  color: #fff;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  background: linear-gradient(180deg, rgba(235,152,93,0.92), rgba(214,120,68,0.80));
  border: 0.5px solid rgba(255,255,255,0.35);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4), 0 8px 20px -8px rgba(217,122,69,0.7);
}
```

- [ ] **Step 2: Replace `frontend/src/training/App.tsx` with the full implementation**

```tsx
import { useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import styles from './App.module.css';

interface TrainingAppProps {
  operatorName?: string;
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#E8955A" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

const CHECKED_SESSIONS = ['Everyday phrases', 'Tech & jargon', 'Names & proper nouns'];

export function TrainingApp({ operatorName = 'Jason' }: TrainingAppProps) {
  const [practiceText, setPracticeText] = useState('');

  return (
    <GlassPanel width={640} height={440}>
      <TitleBar title={<>Training — <strong>{operatorName}</strong></>} />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Sessions</span>
          {CHECKED_SESSIONS.map((name) => (
            <div className={styles.item} key={name}>
              <CheckIcon />
              <span className={styles.name}>{name}</span>
            </div>
          ))}
          <div className={`${styles.item} ${styles.itemCurrent}`}>
            <span className={styles.bullet} />
            <span className={styles.name}>Numbers &amp; units</span>
            <span className={styles.star}>★</span>
          </div>
          <div className={styles.item}>
            <span className={styles.bullet} />
            <span className={`${styles.name} ${styles.nameTrunc}`}>Tricky words &amp; homophones</span>
          </div>
        </div>
        <div className={styles.detail}>
          <span className={styles.intro}>Short read-aloud sessions teach Speakeasy your words.</span>
          <h2 className={styles.headline}>Pick a session on the left, or practice your own words below.</h2>
          <div className={styles.inputRow}>
            <input
              className={styles.textInput}
              type="text"
              placeholder="Your own word or phrase…"
              value={practiceText}
              onChange={(event) => setPracticeText(event.target.value)}
            />
            <button className={styles.practiceBtn} onClick={() => console.log('practice', practiceText)}>
              Practice
            </button>
          </div>
        </div>
      </div>
    </GlassPanel>
  );
}
```

- [ ] **Step 3: Build and verify**

Run: `npm run build`
Expected: exits 0.

With `npm run dev` running, open `http://localhost:5173/training.html`
Expected: sidebar shows 3 coral-checked sessions, "Numbers & units" highlighted with a bullet + gold star, "Tricky words & homophones" truncated with ellipsis. Right pane shows the intro line, the balanced headline, and an input + "Practice" button. Typing in the input works; the border is a subtle default until focused, then switches to the coral ring (`1.5px #E8955A` + soft coral glow).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/training/App.tsx frontend/src/training/App.module.css
git commit -m "Implement training window"
```

---

### Task 11: Final full-build verification pass

**Files:** none (verification only).

- [ ] **Step 1: Clean build from scratch**

Run: `rm -rf frontend/dist frontend/node_modules && cd frontend && npm install && npm run build`
Expected: exits 0, no errors or warnings about missing dependencies.

- [ ] **Step 2: Confirm all 4 entries built**

Run: `ls frontend/dist/*.html`
Expected: `dock.html`, `index.html`, `meetings.html`, `training.html`.

- [ ] **Step 3: Manual visual comparison**

Run: `cd frontend && npm run dev`

Open each of `http://localhost:5173/dock.html`, `/meetings.html`, `/training.html` side by side with `~/Downloads/speakeasy-reference.html`'s corresponding frame (open the reference file directly in a browser tab). Confirm for each: panel dimensions match (360×300 / 720×480 / 640×440), glass blur/gradient/border/shadow read the same, button styles (primary coral/red gradient, secondary glass, small action buttons) match, and the colorful backdrop is visible behind each panel. Toggle the dock panel to recording state (press `R`) and confirm the rainbow waveform animates and the timer ticks.

- [ ] **Step 4: No commit needed** (verification-only task; if any visual mismatch is found, fix it in the relevant component file from the earlier tasks and commit that fix separately).
