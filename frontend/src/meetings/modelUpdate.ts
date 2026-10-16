/** Pure view logic for the "Speech model" section of Settings (no React, no bridge import). */

export type ModelUpdateState =
  | 'idle' | 'checking' | 'up_to_date' | 'same_files' | 'available' | 'offline' | 'error'
  | 'unknown_current' | 'downloading' | 'verifying' | 'ready' | 'cancelled' | 'failed';

export interface ModelUpdateStatus {
  current: { revision: string | null; origin: 'bundled' | 'update' | null; pending: boolean };
  autoCheck: boolean;
  state: ModelUpdateState;
  message: string;
  remote: { revision: string; bytes: number | null; sizeText: string | null } | null;
  progress: number | null;
  /** "1.2 of 2.5 GB", computed in Python so the rounding matches format_gb. */
  progressText: string | null;
  previousRevertible: boolean;
  /** "2.5 GB" for the installed update, or null. */
  updateSize: string | null;
}

export interface ModelUpdateView {
  currentText: string;
  checking: boolean;
  checkDisabled: boolean;
  prompt: { title: string; summary: string; note: string } | null;
  progress: { fraction: number; text: string } | null;
  cancelVisible: boolean;
  message: string;
  messageIsError: boolean;
  useBundledVisible: boolean;
  confirmBody: string;
}

const BUSY: ModelUpdateState[] = ['checking', 'downloading', 'verifying'];
const NOTE =
  'Transcripts may differ slightly from the current model, including for meetings you reprocess. ' +
  'The update is checked before use and takes effect the next time Speakeasy starts.';

export function modelUpdateView(status: ModelUpdateStatus, dismissed: boolean): ModelUpdateView {
  const { current, state } = status;
  const origin = current.origin === 'update' ? 'Update' : current.origin === 'bundled' ? 'Bundled' : 'Unknown';
  const revision = current.revision ? ` · revision ${current.revision}` : '';
  const currentText = `${origin}${revision}${current.pending ? ' (from next launch)' : ''}`;
  const busy = BUSY.includes(state);
  const remote = status.remote;
  const prompt = state === 'available' && !dismissed && remote
    ? {
        title: 'A newer speech model is available.',
        summary: `Revision ${remote.revision}${remote.sizeText ? ` · ${remote.sizeText} download` : ''}.`,
        note: NOTE,
      }
    : null;
  const showMessage = state !== 'available' && state !== 'downloading' && state !== 'checking';
  const size = status.updateSize ? ` (${status.updateSize})` : '';
  return {
    currentText,
    checking: state === 'checking',
    checkDisabled: busy,
    prompt,
    progress: state === 'downloading' && status.progress !== null
      ? { fraction: status.progress, text: status.progressText ?? '' }
      : null,
    cancelVisible: state === 'downloading',
    message: showMessage ? status.message : '',
    messageIsError: state === 'error' || state === 'failed' || state === 'offline',
    useBundledVisible: current.origin === 'update' && !busy,
    confirmBody: `Switch back to the bundled model from the next launch? The downloaded update${size} will be deleted.`,
  };
}

export function createModelUpdateActions(call: (method: string, params?: Record<string, unknown>) => Promise<unknown>) {
  const run = (method: string, params?: Record<string, unknown>) => call(method, params) as Promise<ModelUpdateStatus>;
  return {
    status: () => run('modelUpdate.status'),
    check: () => run('modelUpdate.check'),
    download: () => run('modelUpdate.download'),
    cancel: () => run('modelUpdate.cancel'),
    useBundled: () => run('modelUpdate.useBundled'),
    setAutoCheck: (value: boolean) => run('modelUpdate.setAutoCheck', { value }),
  };
}
