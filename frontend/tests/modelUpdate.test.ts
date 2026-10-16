import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createModelUpdateActions, modelUpdateView } from '../src/meetings/modelUpdate.ts';
import type { ModelUpdateStatus } from '../src/meetings/modelUpdate.ts';

const base: ModelUpdateStatus = {
  current: { revision: '8ae1553', origin: 'bundled', pending: false },
  autoCheck: false, state: 'idle', message: '', remote: null,
  progress: null, progressText: null, previousRevertible: false, updateSize: null,
};
const remote = { revision: 'def5678', bytes: 2500000000, sizeText: '2.5 GB' };
const s = (patch: Partial<ModelUpdateStatus>): ModelUpdateStatus => ({ ...base, ...patch });

test('current model row: bundled, update, and pending', () => {
  assert.equal(modelUpdateView(base, false).currentText, 'Bundled · revision 8ae1553');
  assert.equal(
    modelUpdateView(s({ current: { revision: 'def5678', origin: 'update', pending: false } }), false).currentText,
    'Update · revision def5678');
  assert.equal(
    modelUpdateView(s({ current: { revision: 'def5678', origin: 'update', pending: true } }), false).currentText,
    'Update · revision def5678 (from next launch)');
});

test('idle shows nothing extra; checking disables the button and shows a spinner', () => {
  const v = modelUpdateView(base, false);
  assert.equal(v.prompt, null); assert.equal(v.progress, null); assert.equal(v.message, '');
  assert.equal(v.checking, false);
  const c = modelUpdateView(s({ state: 'checking' }), false);
  assert.equal(c.checking, true); assert.equal(c.checkDisabled, true);
});

test('available shows the prompt card with the brief copy; Not now hides it', () => {
  const v = modelUpdateView(s({ state: 'available', remote }), false);
  assert.equal(v.prompt?.title, 'A newer speech model is available.');
  assert.equal(v.prompt?.summary, 'Revision def5678 · 2.5 GB download.');
  assert.equal(
    v.prompt?.note,
    'Transcripts may differ slightly from the current model, including for meetings you reprocess. ' +
    'The update is checked before use and takes effect the next time Speakeasy starts.');
  assert.equal(modelUpdateView(s({ state: 'available', remote }), true).prompt, null);
});

test('downloading shows progress text and Cancel; verifying shows no Cancel', () => {
  const d = modelUpdateView(s({ state: 'downloading', remote, progress: 0.48, progressText: '1.2 of 2.5 GB' }), false);
  assert.deepEqual(d.progress, { fraction: 0.48, text: '1.2 of 2.5 GB' });
  assert.equal(d.cancelVisible, true);
  assert.equal(d.checkDisabled, true);
  const v = modelUpdateView(s({ state: 'verifying', remote, message: 'Checking the new model…' }), false);
  assert.equal(v.cancelVisible, false);
  assert.equal(v.message, 'Checking the new model…');
  assert.equal(v.progress, null);
});

test('ready, error, offline, failed and cancelled show the message', () => {
  for (const state of ['ready', 'error', 'offline', 'failed', 'cancelled', 'up_to_date', 'same_files', 'unknown_current'] as const) {
    assert.equal(modelUpdateView(s({ state, message: `msg ${state}` }), false).message, `msg ${state}`);
  }
  assert.equal(modelUpdateView(s({ state: 'error', message: 'x' }), false).messageIsError, true);
  assert.equal(modelUpdateView(s({ state: 'ready', message: 'x' }), false).messageIsError, false);
});

test('Use bundled model only when the current model is an update', () => {
  assert.equal(modelUpdateView(base, false).useBundledVisible, false);
  const u = s({ current: { revision: 'def5678', origin: 'update', pending: false }, previousRevertible: true, updateSize: '2.5 GB' });
  const v = modelUpdateView(u, false);
  assert.equal(v.useBundledVisible, true);
  assert.equal(v.confirmBody,
    'Switch back to the bundled model from the next launch? The downloaded update (2.5 GB) will be deleted.');
  assert.equal(modelUpdateView({ ...u, state: 'downloading' }, false).useBundledVisible, false);
});

test('actions call the bridge methods; Cancel calls modelUpdate.cancel', async () => {
  const calls: [string, unknown][] = [];
  const a = createModelUpdateActions(async (m, p) => { calls.push([m, p]); return base; });
  await a.check(); await a.download(); await a.cancel(); await a.useBundled(); await a.setAutoCheck(true);
  assert.deepEqual(calls, [
    ['modelUpdate.check', undefined], ['modelUpdate.download', undefined],
    ['modelUpdate.cancel', undefined], ['modelUpdate.useBundled', undefined],
    ['modelUpdate.setAutoCheck', { value: true }],
  ]);
});
