import { test } from 'node:test';
import assert from 'node:assert/strict';
import { navigationAction } from '../src/meetings/navigation.ts';

const idle = { recording: false, processing: false, startedAt: null, title: null };
const live = { ...idle, recording: true };

test('recording opens Recording now while a meeting records or processes', () => {
  assert.deepEqual(navigationAction('recording', live), { kind: 'recording' });
  assert.deepEqual(navigationAction('recording', { ...idle, processing: true }), { kind: 'recording' });
});

test('recording falls back to Today once the meeting is over', () => {
  assert.deepEqual(navigationAction('recording', idle), { kind: 'today' });
});

test('settings and meeting map through; anything else is ignored', () => {
  assert.deepEqual(navigationAction('settings', idle), { kind: 'settings' });
  assert.deepEqual(navigationAction({ meeting: 'm-1' }, idle), { kind: 'meeting', id: 'm-1' });
  assert.deepEqual(navigationAction(null, idle), { kind: 'none' });
  assert.deepEqual(navigationAction({ meeting: '' } as never, idle), { kind: 'none' });
});
