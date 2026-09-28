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

/*
 * Esc in WKWebView: page handlers call preventDefault on Escape so WebKit
 * treats it as consumed. When Esc instead reaches AppKit as cancelOperation:,
 * Python emits `window.escape`; re-dispatch it as a synthetic keydown on the
 * focused element so the same topmost-overlay handlers run (they stopPropagation,
 * so only one layer closes). A real Escape keydown seen just before is treated
 * as already delivered, so one press never closes two layers.
 */
const ESCAPE_DEDUPE_MS = 200;
let lastRealEscape = -Infinity;
window.addEventListener(
  'keydown',
  (e) => {
    if (e.key === 'Escape' && e.isTrusted) lastRealEscape = performance.now();
  },
  true,
);
bridge.on('window.escape', () => {
  if (performance.now() - lastRealEscape < ESCAPE_DEDUPE_MS) return;
  const target = document.activeElement ?? document.body;
  target.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }),
  );
});
