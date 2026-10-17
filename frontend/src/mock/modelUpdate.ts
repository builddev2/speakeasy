import type { ModelUpdateStatus } from '../meetings/modelUpdate';

/** Fixtures for `npm run dev` at ?state=settings-model-<key>. */
export function mockModelUpdate(): Record<string, ModelUpdateStatus> {
  const BASE: ModelUpdateStatus = {
    current: { revision: '8ae1553', origin: 'bundled', pending: false },
    autoCheck: false, state: 'idle', message: '', remote: null,
    progress: null, progressText: null, previousRevertible: false, updateSize: null,
  };
  const REMOTE = { revision: 'def5678', bytes: 2_500_000_000, sizeText: '2.5 GB' };
  const UPDATED = {
    current: { revision: 'def5678', origin: 'update' as const, pending: false },
    previousRevertible: true, updateSize: '2.5 GB',
  };
  return {
    idle: BASE,
    checking: { ...BASE, state: 'checking' },
    'up-to-date': { ...BASE, state: 'up_to_date', message: 'Up to date.' },
    available: { ...BASE, state: 'available', remote: REMOTE, message: 'A newer speech model is available.' },
    downloading: { ...BASE, state: 'downloading', remote: REMOTE, progress: 0.48, progressText: '1.2 of 2.5 GB' },
    verifying: { ...BASE, state: 'verifying', remote: REMOTE, message: 'Checking the new model…' },
    ready: { ...BASE, state: 'ready', message: 'Ready. It takes effect the next time Speakeasy starts.', current: { ...BASE.current, pending: false } },
    offline: { ...BASE, state: 'offline', message: "You appear to be offline. Speakeasy works offline as usual." },
    error: { ...BASE, state: 'error', message: "Couldn't check for a model update (timed out). Speakeasy works offline as usual." },
    update: { ...BASE, ...UPDATED, autoCheck: true },
    'update-pending': { ...BASE, ...UPDATED, current: { ...UPDATED.current, pending: true } },
  };
}
