// Pure action-item helpers (no imports: run directly by node --test).
// dueBucket mirrors speakeasy/action_items.py due_bucket.

export type Priority = 'high' | 'normal' | 'low';
export interface ActionItem {
  id: number; meetingId: string | null; meetingTitle: string | null; meetingDate: string | null;
  task: string; owner: string; mine: boolean; priority: Priority; status: 'open' | 'done';
  completedAt: string | null; due: string | null; dueSource: 'claude' | 'user' | null;
  duePhrase: string; claudeDue: string | null; notes: string; tags: string[]; meetingTags: string[];
  source: 'summary' | 'manual'; createdAt: string; updatedAt: string;
}
export type DueBucket = 'overdue' | 'today' | 'week' | 'later' | 'none';
export type OwnerFilter = { kind: 'mine' } | { kind: 'all' } | { kind: 'person'; name: string };
export type GroupBy = 'due' | 'meeting' | 'owner' | 'tag' | 'none';
export type SortBy = 'due' | 'priority' | 'meeting' | 'created';
export interface ViewState {
  owner: OwnerFilter; tags: string[]; priorities: Priority[];
  status: 'open' | 'done' | 'all'; query: string; groupBy: GroupBy; sortBy: SortBy;
}
export interface Group { key: string; label: string; items: ActionItem[]; tone?: 'overdue' | 'done' }

export const DEFAULT_VIEW: ViewState = {
  owner: { kind: 'mine' }, tags: [], priorities: [], status: 'open', query: '', groupBy: 'due', sortBy: 'due',
};
const RANK: Record<Priority, number> = { high: 0, normal: 1, low: 2 };
const BUCKETS: [DueBucket, string][] = [
  ['overdue', 'Overdue'], ['today', 'Today'], ['week', 'This week'], ['later', 'Later'], ['none', 'No date'],
];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const UNASSIGNED = 'Unassigned';

function utcNoon(iso: string): Date { return new Date(`${iso}T12:00:00Z`); }

export function addDays(iso: string, n: number): string {
  const d = utcNoon(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function dueBucket(due: string | null, today: string): DueBucket {
  if (!due) return 'none';
  if (due < today) return 'overdue';
  if (due === today) return 'today';
  const toSunday = (7 - utcNoon(today).getUTCDay()) % 7;
  return due <= addDays(today, toSunday) ? 'week' : 'later';
}

export function effectiveTags(item: ActionItem): string[] {
  const seen = new Map<string, string>();
  for (const t of [...item.tags, ...item.meetingTags]) if (!seen.has(t.toLowerCase())) seen.set(t.toLowerCase(), t);
  return [...seen.values()];
}

const MULTI_OWNER = /[&,/]|\band\b/i;

/** Mine single-owner items all show as the user's own label; others show their stored owner. */
export function ownerDisplay(item: ActionItem, userName: string): string {
  if (item.mine && !MULTI_OWNER.test(item.owner)) return userName.trim() || 'Me';
  return item.owner || UNASSIGNED;
}

/** Owner entry an item belongs to: every mine item (shared ones too) is the user's. */
export function ownerKey(item: ActionItem, userName: string): string {
  if (item.mine) return userName.trim() || 'Me';
  return item.owner || UNASSIGNED;
}

export function filterItems(items: ActionItem[], view: ViewState, identitySet: boolean, userName: string): ActionItem[] {
  const tags = view.tags.map((t) => t.toLowerCase());
  const q = view.query.trim().toLowerCase();
  return items.filter((i) => {
    if (view.owner.kind === 'mine' && identitySet && !i.mine) return false;
    if (view.owner.kind === 'person' && ownerKey(i, userName).toLowerCase() !== view.owner.name.toLowerCase()) return false;
    if (tags.length && !effectiveTags(i).some((t) => tags.includes(t.toLowerCase()))) return false;
    if (view.priorities.length && !view.priorities.includes(i.priority)) return false;
    if (view.status !== 'all' && i.status !== view.status) return false;
    if (q && ![i.task, i.notes, i.owner, i.meetingTitle ?? ''].join(' ').toLowerCase().includes(q)) return false;
    return true;
  });
}

function byDue(a: ActionItem, b: ActionItem): number {
  if (a.due === b.due) return 0;
  if (a.due === null) return 1;
  if (b.due === null) return -1;
  return a.due < b.due ? -1 : 1;
}
function desc(a: string | null, b: string | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return a > b ? -1 : 1;
}

export function sortItems(items: ActionItem[], sortBy: SortBy): ActionItem[] {
  const cmp: Record<SortBy, (a: ActionItem, b: ActionItem) => number> = {
    due: (a, b) => byDue(a, b) || RANK[a.priority] - RANK[b.priority] || a.id - b.id,
    priority: (a, b) => RANK[a.priority] - RANK[b.priority] || byDue(a, b) || a.id - b.id,
    meeting: (a, b) => desc(a.meetingDate, b.meetingDate) || a.id - b.id,
    created: (a, b) => desc(a.createdAt, b.createdAt) || a.id - b.id,
  };
  return [...items].sort(cmp[sortBy]);
}

function alpha(a: string, b: string): number { return a.localeCompare(b, undefined, { sensitivity: 'base' }); }
// The user's own entry first, Unassigned last.
const ownerRank = (label: string, me: string) => (label === UNASSIGNED ? 2 : label.toLowerCase() === me.toLowerCase() ? 0 : 1);

export function groupItems(items: ActionItem[], view: ViewState, today: string, userName: string): Group[] {
  const open = items.filter((i) => i.status === 'open');
  const done = items.filter((i) => i.status === 'done')
    .sort((a, b) => desc(a.completedAt, b.completedAt) || a.id - b.id);
  const sorted = (list: ActionItem[]) => sortItems(list, view.sortBy);
  const keyed = (list: ActionItem[], keyOf: (i: ActionItem) => string[]) => {
    const map = new Map<string, ActionItem[]>();
    for (const i of list) for (const k of keyOf(i)) map.set(k, [...(map.get(k) ?? []), i]);
    return map;
  };
  let groups: Group[];
  if (view.groupBy === 'due') {
    groups = BUCKETS.map(([key, label]) => ({
      key, label, items: sorted(open.filter((i) => dueBucket(i.due, today) === key)),
      ...(key === 'overdue' ? { tone: 'overdue' as const } : {}),
    }));
    groups.push({ key: 'done', label: 'Completed', items: done, tone: 'done' });
    return groups.filter((g) => g.items.length > 0);
  }
  if (view.groupBy === 'none') {
    return [{ key: 'all', label: '', items: [...sorted(open), ...done] }].filter((g) => g.items.length > 0);
  }
  const keyOf: (i: ActionItem) => string[] =
    view.groupBy === 'meeting' ? (i) => [i.meetingId ?? '']
    : view.groupBy === 'owner' ? (i) => [ownerKey(i, userName)]
    : (i) => (effectiveTags(i).length ? effectiveTags(i) : ['']);
  const map = keyed(items, keyOf);
  const sample = (k: string) => map.get(k)![0];
  const me = userName.trim() || 'Me';
  const keys = [...map.keys()].sort((a, b) => {
    if (view.groupBy === 'meeting') {
      if (!a || !b) return a ? -1 : b ? 1 : 0;
      return desc(sample(a).meetingDate, sample(b).meetingDate) || alpha(a, b);
    }
    if (view.groupBy === 'owner') return ownerRank(a, me) - ownerRank(b, me) || alpha(a, b);
    return !a ? 1 : !b ? -1 : alpha(a, b);
  });
  return keys.map((k) => {
    const list = map.get(k)!;
    const label = view.groupBy === 'meeting'
      ? (k ? `${sample(k).meetingTitle} · ${sample(k).meetingDate}` : 'No meeting')
      : view.groupBy === 'tag' ? (k || 'Untagged') : k;
    return { key: `${view.groupBy}:${k}`, label,
      items: [...sorted(list.filter((i) => i.status === 'open')), ...list.filter((i) => i.status === 'done')] };
  });
}

export function sidebarCounts(items: ActionItem[], identitySet: boolean, today: string) {
  const open = items.filter((i) => i.status === 'open' && (!identitySet || i.mine));
  return { open: open.length, overdue: open.filter((i) => dueBucket(i.due, today) === 'overdue').length };
}

export function formatDue(due: string): string {
  const d = utcNoon(due);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

export function ownersOf(items: ActionItem[], userName: string): string[] {
  const me = userName.trim() || 'Me';
  const seen = new Map<string, string>();
  for (const i of items) {
    const label = ownerKey(i, userName);
    if (label !== UNASSIGNED && !seen.has(label.toLowerCase())) seen.set(label.toLowerCase(), label);
  }
  const names = [...seen.values()].sort((a, b) => ownerRank(a, me) - ownerRank(b, me) || alpha(a, b));
  return items.some((i) => ownerKey(i, userName) === UNASSIGNED) ? [...names, UNASSIGNED] : names;
}

const SELF_LABELS = new Set(['you', 'me', 'myself']);

/** A saved person filter that no longer matches an owner entry maps to the user's current label. */
export function resolveOwnerName(name: string, owners: string[], userName: string, items: ActionItem[] = []): string {
  if (owners.includes(name)) return name;
  if (items.some((i) => i.mine && i.owner === name)) return userName.trim() || 'Me';
  const key = name.trim().toLowerCase();
  if (SELF_LABELS.has(key) || key === userName.trim().toLowerCase()) return userName.trim() || 'Me';
  return name;
}

export function parseViewState(raw: string | null): ViewState {
  let v: Partial<Record<keyof ViewState, unknown>>;
  try { v = raw ? JSON.parse(raw) : {}; } catch { return DEFAULT_VIEW; }
  if (!v || typeof v !== 'object') return DEFAULT_VIEW;
  const pick = <T,>(x: unknown, ok: readonly T[], d: T): T => (ok.includes(x as T) ? (x as T) : d);
  const o = v.owner as OwnerFilter | undefined;
  const owner: OwnerFilter = o && (o.kind === 'mine' || o.kind === 'all') ? { kind: o.kind }
    : o && o.kind === 'person' && typeof o.name === 'string' ? { kind: 'person', name: o.name } : DEFAULT_VIEW.owner;
  return {
    owner,
    tags: Array.isArray(v.tags) ? v.tags.filter((t): t is string => typeof t === 'string') : [],
    priorities: Array.isArray(v.priorities)
      ? v.priorities.filter((p): p is Priority => p === 'high' || p === 'normal' || p === 'low') : [],
    status: pick(v.status, ['open', 'done', 'all'] as const, DEFAULT_VIEW.status),
    query: typeof v.query === 'string' ? v.query : '',
    groupBy: pick(v.groupBy, ['due', 'meeting', 'owner', 'tag', 'none'] as const, DEFAULT_VIEW.groupBy),
    sortBy: pick(v.sortBy, ['due', 'priority', 'meeting', 'created'] as const, DEFAULT_VIEW.sortBy),
  };
}
