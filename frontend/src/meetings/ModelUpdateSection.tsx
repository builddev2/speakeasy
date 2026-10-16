import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { bridge } from '../bridge';
import { Switch } from '../components/Switch';
import { ConfirmSheet } from './ConfirmSheet';
import { createModelUpdateActions, modelUpdateView, reduceModelUpdate } from './modelUpdate';
import type { ModelUpdateStatus } from './modelUpdate';
import styles from './SettingsSheet.module.css';

interface ModelUpdateSectionProps {
  /** Mock mode (`npm run dev`): show this status and answer actions locally. */
  mockStatus?: ModelUpdateStatus;
}

/** "Speech model" section of Settings: check, download, cancel and revert the offline model. */
export function ModelUpdateSection({ mockStatus }: ModelUpdateSectionProps) {
  const [{ status, actionError }, dispatch] = useReducer(reduceModelUpdate, { status: mockStatus ?? null, actionError: '' });
  const [dismissed, setDismissed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const lastRemote = useRef<string | null>(null);

  const actions = useMemo(
    () => createModelUpdateActions(mockStatus
      ? async () => { throw new Error('bridge not available in mock mode'); }
      : (method, params) => bridge.call(method, params)),
    [mockStatus],
  );

  useEffect(() => {
    if (mockStatus) return;
    let live = true;
    actions.status().then((s) => { if (live) dispatch({ type: 'changed', status: s }); }).catch((err) => console.error('modelUpdate.status failed', err));
    const off = bridge.on('modelUpdate.changed', (payload) => dispatch({ type: 'changed', status: payload as ModelUpdateStatus }));
    return () => { live = false; off(); };
  }, [actions, mockStatus]);

  // "Not now" hides the card only until the next check result.
  useEffect(() => {
    if (status?.state !== 'available') return;
    const key = status.remote?.revision ?? '';
    if (lastRemote.current !== key) { lastRemote.current = key; setDismissed(false); }
  }, [status]);

  if (!status) return null;
  const view = modelUpdateView(status, dismissed);

  function run(action: () => Promise<ModelUpdateStatus>) {
    dispatch({ type: 'clearError' });
    if (mockStatus) return;
    action()
      .then((next) => dispatch({ type: 'changed', status: next }))
      .catch((err) => dispatch({ type: 'actionFailed', message: String(err instanceof Error ? err.message : err) }));
  }

  return (
    <>
      <div className={styles.sectionTitle}>Speech model</div>
      <div className={styles.fieldRow}>
        <span>Current model</span>
        <span>{view.currentText}</span>
      </div>
      <button className={styles.exportButton} disabled={view.checkDisabled} onClick={() => { setDismissed(false); run(actions.check); }}>
        {view.checking && <span className={styles.spinner} aria-hidden="true" />}
        {view.checking ? 'Checking…' : 'Check for update'}
      </button>
      <div className={styles.toggleRow}>
        <span id="auto-check-label">
          Check automatically (weekly)
          <span className={styles.hint}>Contacts Hugging Face once a week. Never downloads without asking.</span>
        </span>
        <Switch checked={status.autoCheck} onChange={(value) => run(() => actions.setAutoCheck(value))} ariaLabelledBy="auto-check-label" />
      </div>

      {view.prompt && (
        <div className={styles.modelCard} role="group" aria-label="Speech model update">
          <div className={styles.modelCardTitle}>{view.prompt.title}</div>
          <div>{view.prompt.summary}</div>
          <div className={styles.modelNote}>{view.prompt.note}</div>
          <div className={styles.modelCardActions}>
            <button className={styles.exportButton} onClick={() => run(actions.download)}>Download update</button>
            <button className={styles.exportButton} onClick={() => setDismissed(true)}>Not now</button>
          </div>
        </div>
      )}

      {view.progress && (
        <div className={styles.modelCard}>
          <div role="status">{view.progress.text}</div>
          <div className={styles.progressTrack} role="progressbar" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={Math.round(view.progress.fraction * 100)}>
            <div className={styles.progressFill} style={{ width: `${Math.round(view.progress.fraction * 100)}%` }} />
          </div>
          {view.cancelVisible && (
            <div className={styles.modelCardActions}>
              <button className={styles.exportButton} onClick={() => run(actions.cancel)}>Cancel</button>
            </div>
          )}
        </div>
      )}

      {view.message && (
        <div role="status" className={styles[view.messageStyle]}>{view.message}</div>
      )}
      {actionError && <div role="status" className={styles.hint}>{actionError}</div>}

      {view.useBundledVisible && (
        <button className={styles.exportButton} onClick={() => setConfirming(true)}>Use bundled model</button>
      )}
      {confirming && (
        <ConfirmSheet
          title="Use bundled model?"
          body={view.confirmBody}
          confirmLabel="Use bundled model"
          onCancel={() => setConfirming(false)}
          onConfirm={() => { setConfirming(false); run(actions.useBundled); }}
        />
      )}
    </>
  );
}
