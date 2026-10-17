import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { RecordingInfo } from '../src/meetings/types.ts';
import { canStartRecording, processingNote, shouldHandOverNotes, trackRecording } from '../src/meetings/recordingState.ts';

const info = (o: Partial<RecordingInfo>): RecordingInfo =>
  ({ recording: false, processing: false, startedAt: null, title: null, ...o });

test('idle ignores a stale bridge startedAt and resets the first-seen time', () => {
  const t = trackRecording(info({ startedAt: '2026-10-02T09:00:00Z' }), '2026-10-02T09:30:00Z', 'NOW');
  assert.deepEqual(t, { active: false, startedAt: null, firstSeen: null });
});

test('recording uses the bridge startedAt', () => {
  const t = trackRecording(info({ recording: true, startedAt: 'B' }), null, 'NOW');
  assert.deepEqual(t, { active: true, startedAt: 'B', firstSeen: 'NOW' });
});

test('null startedAt falls back to when the page first saw it, and keeps it', () => {
  const first = trackRecording(info({ recording: true }), null, 'T1');
  assert.equal(first.startedAt, 'T1');
  const later = trackRecording(info({ processing: true }), first.firstSeen, 'T2');
  assert.deepEqual(later, { active: true, startedAt: 'T1', firstSeen: 'T1' });
});

test('hands notes over only to the meeting they were typed for', () => {
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', '2026-10-10T09:00:00Z'), true);
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', '2026-10-10T10:01:00Z'), false);
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', '2026-10-10T09:00:00.000Z'), true);
  assert.equal(shouldHandOverNotes(false, '2026-10-10T09:00:00Z', '2026-10-10T09:00:00Z'), false);
  // Unknown start on either side: keep today's behaviour; the server check is the backstop.
  assert.equal(shouldHandOverNotes(true, null, '2026-10-10T09:00:00Z'), true);
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', null), true);
});

test('start is allowed whenever nothing is recording', () => {
  assert.equal(canStartRecording(info({})), true);
  assert.equal(canStartRecording(info({ processing: true, pendingProcessing: 1 })), true);
  assert.equal(canStartRecording(info({ recording: true })), false);
});

test('processing note counts earlier meetings while recording', () => {
  assert.equal(processingNote(info({ recording: true, pendingProcessing: 0 })), null);
  assert.equal(processingNote(info({ recording: true, pendingProcessing: 1 })), 'Processing 1 earlier meeting');
  assert.equal(processingNote(info({ recording: true, pendingProcessing: 3 })), 'Processing 3 earlier meetings');
  assert.equal(processingNote(info({ processing: true, pendingProcessing: 1 })), null);
});
