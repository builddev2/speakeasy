import type { MeetingMeta } from '../mock/meetings';

/** A run of rows under one subheading (null: no subheading). */
export interface RowGroup {
  label: string | null;
  rows: MeetingMeta[];
}

/** A collapsible list section. Year sections hold month `children` and no
 *  `groups`; every other section holds `groups` and no `children`. */
export interface Section {
  key: string;
  label: string;
  count: number;
  defaultOpen: boolean;
  groups: RowGroup[];
  children: Section[];
}

/** Section key -> open, as the user last left it. */
export type OpenState = Record<string, boolean>;

const DAY_MS = 86_400_000;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/** 'YYYY-MM-DD' of `ms` in the local time zone. */
export function localIsoDate(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Calendar arithmetic on whole days (UTC midnights), so DST never shifts a day.
function dayNumber(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
}

function parts(day: number): { y: number; m: number; d: number; weekday: number } {
  const date = new Date(day * DAY_MS);
  return { y: date.getUTCFullYear(), m: date.getUTCMonth(), d: date.getUTCDate(), weekday: (date.getUTCDay() + 6) % 7 };
}

function weekOfLabel(day: number): string {
  const monday = parts(day - parts(day).weekday);
  return `Week of ${monday.d} ${MONTHS_SHORT[monday.m]}`;
}

function newSection(key: string, label: string, defaultOpen: boolean): Section {
  return { key, label, count: 0, defaultOpen, groups: [], children: [] };
}

function addRow(section: Section, label: string | null, row: MeetingMeta) {
  section.count += 1;
  const last = section.groups[section.groups.length - 1];
  if (last && last.label === label) last.rows.push(row);
  else section.groups.push({ label, rows: [row] });
}

/** Group `metas` (newest first) into time sections relative to `today` ('YYYY-MM-DD'). */
export function buildSections(metas: MeetingMeta[], today: string): Section[] {
  const t = dayNumber(today);
  const { y: thisYear } = parts(t);
  const thisMonday = t - parts(t).weekday;
  const lastMonday = thisMonday - 7;
  const sections: Section[] = [];
  const byKey = new Map<string, Section>();

  function section(key: string, label: string, defaultOpen: boolean, parent?: Section): Section {
    let s = byKey.get(key);
    if (!s) {
      s = newSection(key, label, defaultOpen);
      byKey.set(key, s);
      (parent ? parent.children : sections).push(s);
    }
    return s;
  }

  for (const row of metas) {
    const n = dayNumber(row.startDate);
    const p = parts(n);
    if (n >= t) addRow(section('today', 'Today', true), null, row);
    else if (n === t - 1) addRow(section('yesterday', 'Yesterday', true), null, row);
    else if (n >= thisMonday) addRow(section('this-week', 'This week', true), WEEKDAYS[p.weekday], row);
    else if (n >= lastMonday)
      addRow(section('last-week', 'Last week', true), `${WEEKDAYS[p.weekday]} ${p.d} ${MONTHS_SHORT[p.m]}`, row);
    else if (p.y === thisYear)
      addRow(section(`m-${p.y}-${pad(p.m + 1)}`, MONTHS[p.m], false), weekOfLabel(n), row);
    else {
      const year = section(`y-${p.y}`, String(p.y), false);
      year.count += 1;
      addRow(section(`m-${p.y}-${pad(p.m + 1)}`, `${MONTHS[p.m]} ${p.y}`, false, year), weekOfLabel(n), row);
    }
  }
  return sections;
}

export function isOpen(section: Section, state: OpenState, expandAll: boolean): boolean {
  return expandAll || (state[section.key] ?? section.defaultOpen);
}

/** Rows a person can see, in display order (keyboard navigation walks these). */
export function visibleRows(sections: Section[], state: OpenState, expandAll: boolean): MeetingMeta[] {
  const out: MeetingMeta[] = [];
  for (const s of sections) {
    if (!isOpen(s, state, expandAll)) continue;
    for (const g of s.groups) out.push(...g.rows);
    out.push(...visibleRows(s.children, state, expandAll));
  }
  return out;
}

/** Keys that must be open for meeting `id` to be visible ([] if absent). */
export function sectionPathFor(sections: Section[], id: string): string[] {
  for (const s of sections) {
    if (s.groups.some((g) => g.rows.some((r) => r.id === id))) return [s.key];
    const inner = sectionPathFor(s.children, id);
    if (inner.length) return [s.key, ...inner];
  }
  return [];
}

/** Stored open state; anything malformed is ignored rather than trusted. */
export function parseOpenState(raw: string | null): OpenState {
  if (!raw) return {};
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    const out: OpenState = {};
    for (const [k, v] of Object.entries(value)) if (typeof v === 'boolean') out[k] = v;
    return out;
  } catch {
    return {};
  }
}
