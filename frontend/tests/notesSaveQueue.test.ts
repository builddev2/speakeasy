import assert from 'node:assert/strict';
import test from 'node:test';
import { queueNotesSave } from '../src/meetings/notesSaveQueue.ts';

test('a queued draft does not save after its editor is taken', async () => {
  let finishFirst!: () => void;
  const first = new Promise<void>((resolve) => { finishFirst = resolve; });
  const sent: string[] = [];
  let closed = false;
  const next = queueNotesSave(first, 'later', () => closed, async (value) => { sent.push(value); });
  closed = true;
  finishFirst();
  await next;
  assert.deepEqual(sent, []);
});

test('later note waits for the earlier save', async () => {
  let finishFirst!: () => void;
  const first = new Promise<void>((resolve) => { finishFirst = resolve; });
  const sent: string[] = [];
  const next = queueNotesSave(first, 'later', () => false, async (value) => { sent.push(value); });
  assert.deepEqual(sent, []);
  finishFirst();
  await next;
  assert.deepEqual(sent, ['later']);
});
