import { useEffect, useRef } from 'react';

/**
 * Escape routing for overlays. Escape must close only the topmost open layer
 * and must not depend on DOM focus (in WKWebView a clicked checkbox/button
 * leaves focus on <body>, so per-element key handlers never see the key).
 *
 * The stack logic is pure (push/pop/top/handleKey) so it can be checked by
 * reading; one capture-phase document listener drives it.
 */
export interface OverlayEntry {
  onEscape: () => void;
}

const stack: OverlayEntry[] = [];

export function push(entry: OverlayEntry): void {
  stack.push(entry);
}

export function pop(entry: OverlayEntry): void {
  const i = stack.lastIndexOf(entry);
  if (i >= 0) stack.splice(i, 1);
}

export function top(): OverlayEntry | undefined {
  return stack[stack.length - 1];
}

/** Returns true when the key was consumed (an overlay handled the Escape). */
export function handleKey(e: { key: string; preventDefault(): void; stopPropagation(): void }): boolean {
  if (e.key !== 'Escape') return false;
  const entry = top();
  if (!entry) return false;
  e.preventDefault();
  e.stopPropagation();
  entry.onEscape();
  return true;
}

if (typeof document !== 'undefined') {
  document.addEventListener('keydown', (e) => void handleKey(e), true);
}

/** Register an overlay while `active`; later registrations sit on top. */
export function useOverlayEscape(active: boolean, onEscape: () => void): void {
  const ref = useRef(onEscape);
  ref.current = onEscape;
  useEffect(() => {
    if (!active) return;
    const entry: OverlayEntry = { onEscape: () => ref.current() };
    push(entry);
    return () => pop(entry);
  }, [active]);
}
