import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { TranscriptLine } from '../src/mock/meetings.ts';
import { formatElapsed, groupTurns } from '../src/meetings/transcriptTurns.ts';

function line(i: number, speakerNumber: number, label: string, start: number): TranscriptLine {
  return { time: `9:00:${String(start).padStart(2, '0')} AM`, speakerNumber, speakerLabel: label,
    text: `t${i}`, segmentIndex: i, confidence: null, overlap: false, start };
}

test('elapsed time is m:ss under an hour and h:mm:ss after', () => {
  assert.equal(formatElapsed(3), '0:03');
  assert.equal(formatElapsed(62.9), '1:02');
  assert.equal(formatElapsed(3735), '1:02:15');
});

test('consecutive lines from one speaker form one turn', () => {
  const lines = [line(0, 1, 'Alex', 3), line(1, 1, 'Alex', 9), line(2, 2, 'Priya', 14), line(3, 1, 'Alex', 27)];
  const turns = groupTurns(lines);
  assert.deepEqual(turns.map((t) => [t.speakerLabel, t.start, t.lines.length]), [['Alex', 3, 2], ['Priya', 14, 1], ['Alex', 27, 1]]);
  assert.equal(turns[0].time, '9:00:03 AM');
});

test('every segment appears exactly once and in order', () => {
  const lines = [line(0, 1, 'A', 0), line(1, 2, 'B', 1), line(2, 2, 'B', 2), line(3, 1, 'A', 3), line(4, 1, 'A', 4)];
  const flat = groupTurns(lines).flatMap((t) => t.lines.map((l) => l.segmentIndex));
  assert.deepEqual(flat, [0, 1, 2, 3, 4]);
});

test('a renamed label with the same number still splits on label change', () => {
  const lines = [line(0, 1, 'Speaker 1', 0), line(1, 1, 'Jordan', 2)];
  assert.equal(groupTurns(lines).length, 2);
});

test('empty transcript gives no turns', () => {
  assert.deepEqual(groupTurns([]), []);
});
