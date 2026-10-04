import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgendaEvent } from '../src/mock/meetings.ts';
import { engineBanner, heroWhen, splitAgenda } from '../src/meetings/agendaSplit.ts';

const base = Date.parse('2026-10-03T12:00:00+01:00');
function ev(key: string, startMin: number, durMin = 30, status: AgendaEvent['status'] = 'none'): AgendaEvent {
  const s = new Date(base + startMin * 60_000), e = new Date(base + (startMin + durMin) * 60_000);
  return { key, time: '', endTime: '', title: key, attendeeCount: 2, status, meetingId: status === 'recorded' ? 'm' : null,
           start: s.toISOString(), end: e.toISOString() };
}

test('in-progress event is the hero; the next three follow; the rest are folded', () => {
  const day = [ev('a', -240), ev('b', -120, 30, 'recorded'), ev('now', -10), ...[1, 2, 3, 4, 5].map((i) => ev(`n${i}`, i * 60))];
  const s = splitAgenda(day, base, false);
  assert.equal(s.hero?.key, 'now'); assert.equal(s.heroKind, 'now');
  assert.deepEqual(s.next.map((e) => e.key), ['n1', 'n2', 'n3']);
  assert.deepEqual(s.more.map((e) => e.key), ['n4', 'n5']);
  assert.deepEqual(s.earlier.map((e) => e.key), ['a', 'b']);
  assert.equal(s.earlierRecorded, 1);
});

test('without an event in progress the next one is the hero', () => {
  const s = splitAgenda([ev('a', -120), ev('x', 30), ev('y', 90)], base, false);
  assert.equal(s.hero?.key, 'x'); assert.equal(s.heroKind, 'next');
  assert.deepEqual(s.next.map((e) => e.key), ['y']);
});

test('a recorded event in progress is not the hero', () => {
  const s = splitAgenda([ev('r', -10, 30, 'recorded'), ev('x', 30)], base, false);
  assert.equal(s.hero?.key, 'x');
  assert.deepEqual(s.next.map((e) => e.key), ['r']);
});

test('while recording, the hero slot belongs to the recording', () => {
  const s = splitAgenda([ev('live', -5, 30, 'recording'), ev('x', 30)], base, true);
  assert.equal(s.hero, null); assert.equal(s.heroKind, null);
  assert.deepEqual(s.next.map((e) => e.key), ['x']);
});

test('end of day: nothing left', () => {
  const s = splitAgenda([ev('a', -120)], base, false);
  assert.equal(s.hero, null); assert.equal(s.next.length, 0); assert.equal(s.earlier.length, 1);
});

test('a busy day of 20 meetings shows at most 4 rows before folding', () => {
  const day = Array.from({ length: 20 }, (_, i) => ev(`e${i}`, (i - 10) * 30, 25));
  const s = splitAgenda(day, base, false);
  assert.ok((s.hero ? 1 : 0) + s.next.length <= 4);
  assert.equal(s.earlier.length + (s.hero ? 1 : 0) + s.next.length + s.more.length, 20);
});

test('heroWhen', () => {
  assert.equal(heroWhen(ev('a', -5), base), 'Now');
  assert.equal(heroWhen(ev('a', 4), base), 'Starts in 4 min');
  assert.match(heroWhen({ ...ev('a', 120), time: '2:00 PM' }, base), /^at 2:00 PM$/);
});

test('engine banners', () => {
  const idle = { recording: false, processing: false, startedAt: null, title: null };
  assert.equal(engineBanner(idle), null);
  assert.deepEqual(engineBanner({ ...idle, mode: 'mic_failed', micFailure: 'device_unavailable' }),
    { text: 'Connect a microphone or select an input, then retry', action: 'retry' });
  assert.equal(engineBanner({ ...idle, mode: 'ready', startError: 'microphone_busy' })?.text,
    'Microphone is still being released. Wait, then try again.');
  assert.equal(engineBanner({ ...idle, mode: 'ready', processingError: 'processing_failed' })?.text,
    'Meeting could not finish normally. Check saved meetings before trying again.');
});
