import type { ActionItem } from '../meetings/actionItems.ts';

// Library-wide mock action items. "Today" is the mock day, 2026-09-29 (a
// Tuesday), matching MOCK_NOW_MS in meetings.ts; meeting ids/titles/dates are
// copied from the mock metas there (not imported: meetings.ts builds its
// details from this list).
export const MOCK_TODAY = '2026-09-29';

const STAND = { meetingId: 'm-standup', meetingTitle: 'Stand-up', meetingDate: '2026-09-29', meetingTags: ['Product'] };
const SPRINT = { meetingId: 'm-sprint-planning', meetingTitle: 'Sprint Planning', meetingDate: '2026-09-28', meetingTags: ['Product', 'Planning'] };
const CALL = { meetingId: 'm-customer-call', meetingTitle: 'Customer Call — Fenwick Labs', meetingDate: '2026-09-24', meetingTags: ['Customer'] };
const NO_MEETING = { meetingId: null, meetingTitle: null, meetingDate: null, meetingTags: [] as string[] };

function make(id: number, p: Partial<ActionItem> & Pick<ActionItem, 'task' | 'owner'>): ActionItem {
  return {
    id, meetingId: null, meetingTitle: null, meetingDate: null, mine: p.owner === 'Jason' || p.owner === 'You', priority: 'normal',
    status: 'open', completedAt: null, due: null, dueSource: null, duePhrase: '', claudeDue: null, notes: '',
    tags: [], meetingTags: [], source: 'summary',
    createdAt: `2026-09-2${id % 10}T09:00:00Z`, updatedAt: `2026-09-2${id % 10}T09:00:00Z`, ...p,
  };
}

export const MOCK_ACTION_ITEMS: ActionItem[] = [
  // overdue
  make(1, { ...STAND, task: 'Fix offline sync drop on airplane mode', owner: 'Sam', priority: 'high',
    due: '2026-09-25', dueSource: 'claude', claudeDue: '2026-09-25', duePhrase: 'before the release', tags: ['Mobile'] }),
  // this week, with a spoken phrase
  make(2, { ...STAND, task: 'Ship revenue dashboard', owner: 'Priya',
    due: '2026-10-02', dueSource: 'claude', claudeDue: '2026-10-02', duePhrase: 'by Friday' }),
  // due today
  make(3, { ...SPRINT, task: 'Add a device-testing buffer to sprint estimates', owner: 'Jason', priority: 'high',
    due: '2026-09-29', dueSource: 'user', claudeDue: null, tags: ['Estimates'] }),
  // later, with a user override of Claude's date
  make(4, { ...CALL, task: 'Send Fenwick the calendar integration timeline', owner: 'Morgan',
    due: '2026-10-20', dueSource: 'user', claudeDue: '2026-10-06', duePhrase: 'next week', notes: 'Include the offline caveat.' }),
  // no date
  make(5, { ...CALL, task: 'Schedule follow-up demo', owner: 'You', priority: 'low' }),
  make(6, { ...SPRINT, task: 'Move offline sync fix to top of backlog', owner: 'Alex', priority: 'high', tags: ['Mobile'] }),
  // manual, no meeting
  make(7, { ...NO_MEETING, task: 'Renew the team domain', owner: 'Jason', source: 'manual',
    due: '2026-10-01', dueSource: 'user', tags: ['Admin'] }),
  make(8, { ...NO_MEETING, task: 'Book a venue for the team offsite', owner: '', source: 'manual' }),
  // done
  make(9, { ...STAND, task: 'Book the review room', owner: 'Jason', status: 'done',
    completedAt: '2026-09-29T10:15:00Z' }),
  make(10, { ...SPRINT, task: 'Split onboarding tickets by screen', owner: 'Priya', status: 'done',
    completedAt: '2026-09-28T17:00:00Z', due: '2026-09-28', dueSource: 'claude', claudeDue: '2026-09-28' }),
];
