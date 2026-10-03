import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { RecordingInfo } from '../src/mock/meetings.ts';
import { trackRecording } from '../src/meetings/recordingState.ts';

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
