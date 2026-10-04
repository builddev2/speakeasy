import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildSections,
  isOpen,
  localIsoDate,
  parseOpenState,
  sectionPathFor,
  visibleRows,
} from '../src/meetings/listSections.ts';
import type { MeetingMeta } from '../src/mock/meetings.ts';

function meta(id: string, startDate: string): MeetingMeta {
  return {
    id, title: id, dayLabel: '', startDate, time: '9:00 AM', duration: '30 min',
    subtitle: '', speakerCount: 1, hasSummary: false, approximate: false, tags: [], people: [],
  };
}
// Newest first, as the bridge sends them.
function metas(...dates: string[]): MeetingMeta[] {
  return dates.map((d, i) => meta(`m${i}`, d));
}
const keys = (s: ReturnType<typeof buildSections>) => s.map((x) => x.key);

// Saturday 3 Oct 2026: this week = Mon 28 Sep..; last week = 21–27 Sep.
const SAT = '2026-10-03';

test('recent sections, Monday week start, labels and counts', () => {
  const s = buildSections(
    metas('2026-10-03', '2026-10-02', '2026-10-01', '2026-09-28', '2026-09-27', '2026-09-21', '2026-09-20'),
    SAT,
  );
  assert.deepEqual(keys(s), ['today', 'yesterday', 'this-week', 'last-week', 'm-2026-09']);
  assert.deepEqual(s.map((x) => x.label), ['Today', 'Yesterday', 'This week', 'Last week', 'September']);
  assert.deepEqual(s.map((x) => x.count), [1, 1, 2, 2, 1]);
  assert.deepEqual(s.map((x) => x.defaultOpen), [true, true, true, true, false]);
  assert.deepEqual(s[2].groups.map((g) => g.label), ['Thursday', 'Monday']);
  assert.deepEqual(s[3].groups.map((g) => g.label), ['Sunday 27 Sep', 'Monday 21 Sep']);
  assert.deepEqual(s[0].groups.map((g) => g.label), [null]);
});

test('today is a Monday: yesterday (Sunday) wins over last week; no this-week section', () => {
  const s = buildSections(metas('2026-09-28', '2026-09-27', '2026-09-26'), '2026-09-28');
  assert.deepEqual(keys(s), ['today', 'yesterday', 'last-week']);
  assert.equal(s[2].count, 1);
});

test('today is a Sunday: this week runs Monday..Friday', () => {
  const s = buildSections(metas('2026-10-02', '2026-09-28', '2026-09-27'), '2026-10-04');
  assert.deepEqual(keys(s), ['this-week', 'last-week']);
  assert.equal(s[0].count, 2);
});

test('older months this year: collapsed, grouped by week, Monday may be in the previous month', () => {
  const s = buildSections(metas('2026-09-02', '2026-09-01', '2026-08-31', '2026-08-04'), SAT);
  assert.deepEqual(keys(s), ['m-2026-09', 'm-2026-08']);
  assert.deepEqual(s[0].groups.map((g) => g.label), ['Week of 31 Aug']);
  assert.equal(s[0].count, 2);
  assert.deepEqual(s[1].groups.map((g) => g.label), ['Week of 31 Aug', 'Week of 3 Aug']);
});

test('earlier years: collapsed year with collapsed month children', () => {
  const s = buildSections(metas('2026-01-05', '2025-12-15', '2025-11-12', '2024-03-01'), SAT);
  assert.deepEqual(keys(s), ['m-2026-01', 'y-2025', 'y-2024']);
  const y = s[1];
  assert.equal(y.label, '2025');
  assert.equal(y.count, 2);
  assert.equal(y.defaultOpen, false);
  assert.deepEqual(y.groups, []);
  assert.deepEqual(y.children.map((c) => [c.key, c.label, c.count, c.defaultOpen]), [
    ['m-2025-12', 'December 2025', 1, false],
    ['m-2025-11', 'November 2025', 1, false],
  ]);
});

test('New Year: last week can span years; older December goes to the year section', () => {
  // Fri 2 Jan 2026: this week = Mon 29 Dec 2025..; last week = 22–28 Dec 2025.
  const s = buildSections(metas('2026-01-01', '2025-12-29', '2025-12-22', '2025-12-21'), '2026-01-02');
  assert.deepEqual(keys(s), ['yesterday', 'this-week', 'last-week', 'y-2025']);
  assert.deepEqual(s[1].groups.map((g) => g.label), ['Monday']);
  assert.deepEqual(s[3].children.map((c) => c.key), ['m-2025-12']);
});

test('future-dated meetings fall into today; empty input gives no sections', () => {
  assert.deepEqual(keys(buildSections(metas('2026-10-05'), SAT)), ['today']);
  assert.deepEqual(buildSections([], SAT), []);
});

test('visibleRows respects collapsed sections, year months and expandAll', () => {
  const s = buildSections(metas('2026-10-03', '2026-08-04', '2025-11-12'), SAT);
  assert.deepEqual(visibleRows(s, {}, false).map((m) => m.id), ['m0']);
  assert.deepEqual(visibleRows(s, { 'm-2026-08': true }, false).map((m) => m.id), ['m0', 'm1']);
  // An open year with a closed month still hides that month's rows.
  assert.deepEqual(visibleRows(s, { 'y-2025': true }, false).map((m) => m.id), ['m0']);
  assert.deepEqual(visibleRows(s, { 'y-2025': true, 'm-2025-11': true }, false).map((m) => m.id), ['m0', 'm2']);
  // An open month inside a closed year stays hidden.
  assert.deepEqual(visibleRows(s, { 'm-2025-11': true }, false).map((m) => m.id), ['m0']);
  assert.deepEqual(visibleRows(s, { today: false }, true).map((m) => m.id), ['m0', 'm1', 'm2']);
  assert.equal(isOpen(s[0], { today: false }, false), false);
  assert.equal(isOpen(s[0], {}, false), true);
});

test('sectionPathFor gives the keys to open', () => {
  const s = buildSections(metas('2026-10-03', '2026-08-04', '2025-11-12'), SAT);
  assert.deepEqual(sectionPathFor(s, 'm0'), ['today']);
  assert.deepEqual(sectionPathFor(s, 'm1'), ['m-2026-08']);
  assert.deepEqual(sectionPathFor(s, 'm2'), ['y-2025', 'm-2025-11']);
  assert.deepEqual(sectionPathFor(s, 'missing'), []);
});

test('parseOpenState tolerates junk', () => {
  assert.deepEqual(parseOpenState(null), {});
  assert.deepEqual(parseOpenState('not json'), {});
  assert.deepEqual(parseOpenState('[1,2]'), {});
  assert.deepEqual(parseOpenState('"x"'), {});
  assert.deepEqual(parseOpenState('{"a":true,"b":"yes","c":false}'), { a: true, c: false });
});

test('localIsoDate uses local calendar fields', () => {
  assert.equal(localIsoDate(new Date(2026, 0, 2, 23, 59).getTime()), '2026-01-02');
});
