// frontend/tests/actionItems.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_VIEW, addDays, dueBucket, effectiveTags, filterItems, formatDue, groupItems,
  ownersOf, parseViewState, sidebarCounts, sortItems,
} from '../src/meetings/actionItems.ts';
import type { ActionItem, ViewState } from '../src/meetings/actionItems.ts';

let n = 0;
function item(p: Partial<ActionItem>): ActionItem {
  n += 1;
  return {
    id: n, meetingId: 'm1', meetingTitle: 'Sync', meetingDate: '2026-10-05', task: `t${n}`, owner: '',
    mine: false, priority: 'normal', status: 'open', completedAt: null, due: null, dueSource: null,
    duePhrase: '', claudeDue: null, notes: '', tags: [], meetingTags: [], source: 'summary',
    createdAt: `2026-10-0${(n % 9) + 1}T10:00:00Z`, updatedAt: '', ...p,
  };
}
const view = (p: Partial<ViewState> = {}): ViewState => ({ ...DEFAULT_VIEW, ...p });
const SUN = '2026-10-11', MON = '2026-10-12';

test('addDays crosses months', () => {
  assert.equal(addDays('2026-10-31', 1), '2026-11-01');
  assert.equal(addDays('2026-03-01', -1), '2026-02-28');
});

test('dueBucket week boundaries (Monday-start weeks)', () => {
  assert.equal(dueBucket(null, SUN), 'none');
  assert.equal(dueBucket(SUN, SUN), 'today');
  assert.equal(dueBucket(SUN, MON), 'overdue');
  assert.equal(dueBucket(MON, SUN), 'later');
  assert.equal(dueBucket('2026-10-18', MON), 'week');
  assert.equal(dueBucket('2026-10-19', MON), 'later');
});

test('effectiveTags merges case-insensitively, own spelling wins', () => {
  assert.deepEqual(effectiveTags(item({ tags: ['budget'], meetingTags: ['Budget', 'Ops'] })), ['budget', 'Ops']);
});

test('filter: mine falls back to everyone when no identity', () => {
  const items = [item({ mine: true }), item({ mine: false })];
  assert.equal(filterItems(items, view(), true).length, 1);
  assert.equal(filterItems(items, view(), false).length, 2);
});

test('filter: person, Unassigned, tags ANY, priority, status, query', () => {
  const a = item({ owner: 'Siam', tags: ['X'], priority: 'high', notes: 'call the BANK' });
  const b = item({ owner: '', meetingTags: ['Y'] });
  const c = item({ owner: 'Siam', status: 'done' });
  const all = [a, b, c];
  const base = { owner: { kind: 'all' } as const };
  assert.deepEqual(filterItems(all, view({ ...base, owner: { kind: 'person', name: 'siam' } }), true), [a]);
  assert.deepEqual(filterItems(all, view({ ...base, owner: { kind: 'person', name: 'Unassigned' } }), true), [b]);
  assert.deepEqual(filterItems(all, view({ ...base, tags: ['x', 'y'] }), true), [a, b]);
  assert.deepEqual(filterItems(all, view({ ...base, priorities: ['high'] }), true), [a]);
  assert.deepEqual(filterItems(all, view({ ...base, status: 'done' }), true), [c]);
  assert.equal(filterItems(all, view({ ...base, status: 'all' }), true).length, 3);
  assert.deepEqual(filterItems(all, view({ ...base, query: 'bank' }), true), [a]);
  assert.deepEqual(filterItems(all, view({ ...base, query: 'sync' }), true), [a, b]);   // meeting title
});

test('sort: due nulls last, priority, meeting newest, created newest', () => {
  const a = item({ due: '2026-10-20', priority: 'low', meetingDate: '2026-10-01' });
  const b = item({ due: null, priority: 'high', meetingDate: '2026-10-09' });
  const c = item({ due: '2026-10-13', priority: 'normal', meetingDate: null });
  assert.deepEqual(sortItems([a, b, c], 'due').map((i) => i.id), [c.id, a.id, b.id]);
  assert.deepEqual(sortItems([a, b, c], 'priority').map((i) => i.id), [b.id, c.id, a.id]);
  assert.deepEqual(sortItems([a, b, c], 'meeting').map((i) => i.id), [b.id, a.id, c.id]);
  const d1 = item({ createdAt: '2026-10-01T10:00:00Z' }), d2 = item({ createdAt: '2026-10-08T10:00:00Z' }),
    d3 = item({ createdAt: '2026-10-05T10:00:00Z' });
  assert.deepEqual(sortItems([d1, d2, d3], 'created').map((i) => i.id), [d2.id, d3.id, d1.id]);
});

test('group by due: order, labels, completed last, empty groups dropped', () => {
  const items = [item({ due: '2026-10-09' }), item({ due: MON }), item({ due: null }),
    item({ status: 'done', completedAt: '2026-10-10T00:00:00Z' })];
  const groups = groupItems(items, view({ status: 'all', owner: { kind: 'all' } }), MON);
  assert.deepEqual(groups.map((g) => g.key), ['overdue', 'today', 'none', 'done']);
  assert.deepEqual(groups.map((g) => g.label), ['Overdue', 'Today', 'No date', 'Completed']);
  assert.equal(groups[0].tone, 'overdue');
});

test('group by tag puts an item under each tag, Untagged last', () => {
  const groups = groupItems([item({ tags: ['B', 'A'] }), item({})], view({ groupBy: 'tag' }), MON);
  assert.deepEqual(groups.map((g) => g.label), ['A', 'B', 'Untagged']);
});

test('group by meeting and owner', () => {
  const items = [item({ meetingId: null, meetingTitle: null, meetingDate: null, source: 'manual' }),
    item({ meetingId: 'm2', meetingTitle: 'Later', meetingDate: '2026-10-09', owner: 'Zed' })];
  assert.deepEqual(groupItems(items, view({ groupBy: 'meeting' }), MON).map((g) => g.label),
    ['Later · 2026-10-09', 'No meeting']);
  assert.deepEqual(groupItems(items, view({ groupBy: 'owner' }), MON).map((g) => g.label), ['Zed', 'Unassigned']);
});

test('sidebarCounts counts open items (mine when identity set) and overdue', () => {
  const items = [item({ mine: true, due: '2026-10-01' }), item({ mine: true }), item({ mine: false, due: '2026-10-01' }),
    item({ mine: true, status: 'done', due: '2026-10-01' })];
  assert.deepEqual(sidebarCounts(items, true, MON), { open: 2, overdue: 1 });
  assert.deepEqual(sidebarCounts(items, false, MON), { open: 3, overdue: 2 });
});

test('formatDue and ownersOf', () => {
  assert.equal(formatDue('2026-10-16'), 'Fri 16 Oct');
  assert.deepEqual(ownersOf([item({ owner: 'b' }), item({ owner: '' }), item({ owner: 'A' }), item({ owner: 'B' })]),
    ['A', 'b', 'Unassigned']);
});

test('parseViewState falls back on junk and keeps valid fields', () => {
  assert.deepEqual(parseViewState(null), DEFAULT_VIEW);
  assert.deepEqual(parseViewState('{not json'), DEFAULT_VIEW);
  const v = parseViewState(JSON.stringify({ ...DEFAULT_VIEW, groupBy: 'tag', sortBy: 'bogus', tags: ['A', 3] }));
  assert.equal(v.groupBy, 'tag'); assert.equal(v.sortBy, 'due'); assert.deepEqual(v.tags, ['A']);
});
