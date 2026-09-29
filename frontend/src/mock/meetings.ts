// Mock data + the TypeScript contract Task 10's Python backend must emit
// exactly. Used only in mock mode (`!bridge.embedded`) so the redesigned UI
// can be reviewed against realistic fake data before anything is wired up.

export interface TranscriptLine {
  time: string;
  speakerNumber: number;
  speakerLabel: string;
  text: string;
  segmentIndex: number;
  confidence: number | null;
  overlap: boolean;
  start: number; // seconds, for jump-to
}

export interface MeetingMeta {
  id: string;
  title: string;
  dayLabel: string;
  time: string;
  duration: string;
  subtitle: string;
  speakerCount: number;
  hasSummary: boolean;
  approximate: boolean;
  tags: string[];
  people: string[];
}

export interface MeetingDetail extends MeetingMeta {
  date: string;
  lines: TranscriptLine[];
  summary: string | null;
  actionItems: string[];
  event: { title: string; time: string } | null; // phase 3; null until then
}

export interface Filters {
  total: number;
  tags: { name: string; count: number }[];
  people: { name: string; count: number }[];
  features: { calendar: boolean; claude: boolean; settings: boolean };
}

export interface SearchResult {
  meetingId: string;
  title: string;
  dayLabel: string;
  time: string;
  kind: 'transcript' | 'notes';
  speaker: string | null;
  alsoSpeakers: string[];
  seconds: number | null;
  segmentIndex: number | null;
  parts: { text: string; hit: boolean }[];
}

export interface LibraryStatus {
  state: 'idle' | 'upgrading' | 'done' | 'failed';
  done: number;
  total: number;
  skipped: { file: string; reason: string }[];
}

export interface AgendaEvent {
  // phase 3
  key: string;
  time: string;
  /** Null when no end time is known yet (e.g. a further-out Upcoming row). */
  endTime: string | null;
  title: string;
  attendeeCount: number;
  status: 'recorded' | 'recording' | 'record' | 'none';
  meetingId: string | null;
}

export const SPEAKER_PALETTE = [
  'var(--sp-1)',
  'var(--sp-2)',
  'var(--sp-3)',
  'var(--sp-4)',
  'var(--sp-5)',
  'var(--sp-6)',
  'var(--sp-7)',
];

export function speakerColor(speakerNumber: number, colorCodeSpeakers: boolean): string {
  if (!colorCodeSpeakers) return 'var(--text-mid)';
  return SPEAKER_PALETTE[(speakerNumber - 1) % SPEAKER_PALETTE.length];
}

function ln(
  time: string,
  start: number,
  speakerNumber: number,
  speakerLabel: string,
  text: string,
  segmentIndex: number,
  opts: Partial<Pick<TranscriptLine, 'confidence' | 'overlap'>> = {},
): TranscriptLine {
  return {
    time,
    start,
    speakerNumber,
    speakerLabel,
    text,
    segmentIndex,
    confidence: opts.confidence ?? null,
    overlap: opts.overlap ?? false,
  };
}

// ---------------------------------------------------------------------------
// Meeting metas (list). Covers Today, Yesterday, a weekday and last month.
// ---------------------------------------------------------------------------

const standupMeta: MeetingMeta = {
  id: 'm-standup',
  title: 'Stand-up',
  dayLabel: 'Today',
  time: '9:00 AM',
  duration: '12 min',
  subtitle: 'Alex, Priya, Sam',
  speakerCount: 3,
  hasSummary: true,
  approximate: false,
  tags: ['Product'],
  people: ['Alex', 'Priya', 'Sam'],
};

const oneOnOneMeta: MeetingMeta = {
  id: 'm-1on1-alex',
  title: 'Weekly 1:1 — Alex',
  dayLabel: 'Today',
  time: '1:00 PM',
  duration: '23 min',
  subtitle: 'Alex, Jordan',
  speakerCount: 2,
  hasSummary: true,
  approximate: false,
  tags: ['1:1'],
  people: ['Alex', 'Jordan'],
};

const designReviewMeta: MeetingMeta = {
  id: 'm-design-review',
  title: 'Design Review — Onboarding Flow',
  dayLabel: 'Yesterday',
  time: '11:30 AM',
  duration: '31 min',
  subtitle: 'Priya, Sam, Morgan',
  speakerCount: 3,
  hasSummary: false,
  approximate: false,
  tags: ['Design'],
  people: ['Priya', 'Sam', 'Morgan'],
};

const sprintPlanningMeta: MeetingMeta = {
  id: 'm-sprint-planning',
  title: 'Sprint Planning',
  dayLabel: 'Yesterday',
  time: '3:00 PM',
  duration: '45 min',
  subtitle: 'Alex, Priya, Sam, Jordan',
  speakerCount: 4,
  hasSummary: true,
  approximate: false,
  tags: ['Product', 'Planning'],
  people: ['Alex', 'Priya', 'Sam', 'Jordan'],
};

const customerCallMeta: MeetingMeta = {
  id: 'm-customer-call',
  title: 'Customer Call — Fenwick Labs',
  dayLabel: 'Thursday',
  time: '2:15 PM',
  duration: '18 min',
  subtitle: 'Morgan, Priya',
  speakerCount: 2,
  hasSummary: true,
  approximate: true,
  tags: ['Customer'],
  people: ['Morgan', 'Priya'],
};

const retroMeta: MeetingMeta = {
  id: 'm-retro',
  title: 'Retro — Sprint 14',
  dayLabel: 'Tuesday',
  time: '4:00 PM',
  duration: '27 min',
  subtitle: 'Alex, Priya, Sam, Jordan',
  speakerCount: 4,
  hasSummary: true,
  approximate: false,
  tags: ['Product'],
  people: ['Alex', 'Priya', 'Sam', 'Jordan'],
};

const q3KickoffMeta: MeetingMeta = {
  id: 'm-q3-kickoff',
  title: 'Q3 Kickoff',
  dayLabel: 'August 2026',
  time: '10:00 AM',
  duration: '52 min',
  subtitle: 'Alex, Priya, Sam, Jordan, Morgan',
  speakerCount: 5,
  hasSummary: true,
  approximate: false,
  tags: ['Planning'],
  people: ['Alex', 'Priya', 'Sam', 'Jordan', 'Morgan'],
};

const onboardingSamMeta: MeetingMeta = {
  id: 'm-onboarding-sam',
  title: 'Onboarding — Sam',
  dayLabel: 'August 2026',
  time: '9:30 AM',
  duration: '34 min',
  subtitle: 'Sam, Jordan',
  speakerCount: 2,
  hasSummary: false,
  approximate: false,
  tags: [],
  people: ['Sam', 'Jordan'],
};

export const MOCK_METAS: MeetingMeta[] = [
  standupMeta,
  oneOnOneMeta,
  designReviewMeta,
  sprintPlanningMeta,
  customerCallMeta,
  retroMeta,
  q3KickoffMeta,
  onboardingSamMeta,
];

// ---------------------------------------------------------------------------
// Meeting details (one per meta, keyed by id).
// ---------------------------------------------------------------------------

function detail(meta: MeetingMeta, extra: Omit<MeetingDetail, keyof MeetingMeta>): MeetingDetail {
  return { ...meta, ...extra };
}

const standupDetail = detail(standupMeta, {
  date: 'Sun 27 Sep',
  event: { title: 'Stand-up', time: '9:00 AM' },
  summary:
    'Quick sync on sprint progress. Mobile offline sync is still dropping on airplane mode and is now a P1. ' +
    'Revenue dashboard is on track for Friday. No other blockers.',
  actionItems: ['Fix offline sync drop on airplane mode (Sam)', 'Ship revenue dashboard by Friday (Priya)'],
  lines: [
    ln('9:00:03 AM', 3, 1, 'Alex', "Morning — let's keep this quick. Priya, how's the dashboard?", 0),
    ln('9:00:14 AM', 14, 2, 'Priya', "On track. I'll have it in review by Friday.", 1),
    ln('9:00:27 AM', 27, 3, 'Sam', 'One blocker on my end — offline sync still drops on airplane mode.', 2),
    ln('9:00:41 AM', 41, 1, 'Alex', "Let's call that a P1 and pick it up right after this.", 3),
    ln('9:00:55 AM', 55, 3, 'Sam', 'Works for me, I have a repro already.', 4),
    ln('9:01:10 AM', 70, 2, 'Priya', "I'll ping design about the empty state copy too.", 5),
    ln('9:01:22 AM', 82, 1, 'Alex', "Great, that's it — thanks everyone.", 6),
  ],
});

const oneOnOneDetail = detail(oneOnOneMeta, {
  date: 'Sun 27 Sep',
  event: { title: 'Weekly 1:1 — Alex', time: '1:00 PM' },
  summary:
    'Alex and Jordan covered career growth goals for the quarter, the staffing gap on the mobile team, ' +
    'and agreed to revisit the roadmap after the offline-sync fix ships.',
  actionItems: ["Draft Q4 growth plan (Jordan)", 'Confirm mobile hire start date (Alex)'],
  lines: [
    ln('1:00:05 PM', 5, 1, 'Alex', "How are you feeling about the quarter so far?", 0),
    ln('1:00:19 PM', 19, 4, 'Jordan', 'Good overall. I want to talk about growth goals for Q4.', 1),
    ln('1:00:33 PM', 33, 1, 'Alex', "Let's dig into that. What are you thinking?", 2),
    ln('1:01:02 PM', 62, 4, 'Jordan', "More ownership on the mobile roadmap, and mentoring the new hire.", 3),
    ln('1:01:20 PM', 80, 1, 'Alex', "I like that. Let's put together a plan and revisit next week.", 4),
  ],
});

const designReviewDetail = detail(designReviewMeta, {
  date: 'Sat 26 Sep',
  event: null,
  summary: null,
  actionItems: [],
  lines: [
    ln('11:30:08 AM', 8, 2, 'Priya', "Let's walk through the new onboarding flow.", 0),
    ln('11:30:24 AM', 24, 3, 'Sam', 'The second screen feels a little dense to me.', 1),
    ln('11:30:40 AM', 40, 5, 'Morgan', "Agreed — could we split it into two steps?", 2),
    ln('11:31:01 AM', 61, 2, 'Priya', "Let's try that and review again Thursday.", 3),
  ],
});

const sprintPlanningDetail = detail(sprintPlanningMeta, {
  date: 'Sat 26 Sep',
  event: { title: 'Sprint Planning', time: '3:00 PM' },
  summary:
    'Planned sprint 15: offline sync fix, dashboard polish and onboarding flow rework carry over. ' +
    'Team agreed to hold scope steady given the mobile hire starts mid-sprint.',
  actionItems: ['Move offline sync fix to top of backlog (Alex)', 'Split onboarding tickets by screen (Sam)'],
  lines: [
    ln('3:00:10 PM', 10, 1, 'Alex', "Let's size the offline sync fix first.", 0),
    ln('3:00:28 PM', 28, 3, 'Sam', "I'd call it a five — there's real device testing involved.", 1),
    ln('3:00:44 PM', 44, 2, 'Priya', "Agreed. Let's put it at the top of the backlog.", 2),
    ln('3:01:05 PM', 65, 4, 'Jordan', "I can help test once the new hire is ramped.", 3),
    ln('3:01:22 PM', 82, 1, 'Alex', "Perfect — let's lock the rest of the sprint in.", 4),
  ],
});

const customerCallDetail = detail(customerCallMeta, {
  date: 'Thu 24 Sep',
  event: { title: 'Customer Call — Fenwick Labs', time: '2:15 PM' },
  summary:
    'Fenwick Labs reported the export feature is working well for their team. They asked about calendar ' +
    'integration timing and requested a follow-up demo once Today view ships.',
  actionItems: ['Send Fenwick the calendar integration timeline (Morgan)', 'Schedule follow-up demo (Priya)'],
  lines: [
    ln('2:15:00 PM', 0, 5, 'Morgan', "Thanks for hopping on — how has export been working for you?", 0),
    ln('2:15:22 PM', 22, 6, 'Priya', "Really well. Our team asked about calendar integration though.", 1),
    ln('2:15:50 PM', 50, 5, 'Morgan', "It's in progress — I can send you a rough timeline after this call.", 2),
    ln('2:16:15 PM', 75, 6, 'Priya', "That would be great, thank you.", 3),
  ],
});

const retroDetail = detail(retroMeta, {
  date: 'Tue 22 Sep',
  event: null,
  summary:
    'Dashboard shipped a day early. The offline sync bug took longer than planned; the team agreed to budget ' +
    'more time for device testing on future sprints.',
  actionItems: ['Add a device-testing buffer to sprint estimates (Jordan)'],
  lines: [
    ln('4:00:06 PM', 6, 1, 'Alex', "What went well this sprint?", 0),
    ln('4:00:20 PM', 20, 2, 'Priya', "Dashboard shipped a day early, that felt good.", 1),
    ln('4:00:35 PM', 35, 3, 'Sam', "The offline sync bug ate more time than expected though.", 2),
    ln('4:00:52 PM', 52, 4, 'Jordan', "Agreed — let's budget more time for device testing next time.", 3),
  ],
});

const q3KickoffDetail = detail(q3KickoffMeta, {
  date: 'Wed 12 Aug',
  event: { title: 'Q3 Kickoff', time: '10:00 AM' },
  summary:
    'Kicked off Q3 priorities: mobile reliability, the onboarding rework, and a customer-facing export feature. ' +
    'Team agreed on rough milestones for each and a mid-quarter check-in.',
  actionItems: ['Draft mobile reliability milestones (Sam)', 'Schedule mid-quarter check-in (Alex)'],
  lines: [
    ln('10:00:12 AM', 12, 1, 'Alex', "Let's set priorities for the quarter.", 0),
    ln('10:00:30 AM', 30, 3, 'Sam', "Mobile reliability has to be top of the list.", 1),
    ln('10:00:48 AM', 48, 2, 'Priya', "Agreed, and I'd add the onboarding rework.", 2),
    ln('10:01:10 AM', 70, 5, 'Morgan', "Customers keep asking for export too — can we fit that in?", 3),
    ln('10:01:30 AM', 90, 1, 'Alex', "Let's aim for all three with a check-in at mid-quarter.", 4),
  ],
});

const onboardingSamDetail = detail(onboardingSamMeta, {
  date: 'Tue 4 Aug',
  event: null,
  summary: null,
  actionItems: [],
  lines: [
    ln('9:30:05 AM', 5, 3, 'Sam', "Excited to get started — where should I look first?", 0),
    ln('9:30:20 AM', 20, 4, 'Jordan', "Start with the onboarding doc, then the codebase tour.", 1),
    ln('9:30:40 AM', 40, 3, 'Sam', "Sounds good, thanks for walking me through it.", 2),
  ],
});

export const MOCK_DETAILS: Record<string, MeetingDetail> = {
  [standupDetail.id]: standupDetail,
  [oneOnOneDetail.id]: oneOnOneDetail,
  [designReviewDetail.id]: designReviewDetail,
  [sprintPlanningDetail.id]: sprintPlanningDetail,
  [customerCallDetail.id]: customerCallDetail,
  [retroDetail.id]: retroDetail,
  [q3KickoffDetail.id]: q3KickoffDetail,
  [onboardingSamDetail.id]: onboardingSamDetail,
};

// ---------------------------------------------------------------------------
// Filters (sidebar tags/people counts). Counts are derived from MOCK_METAS
// so they're always checkable against the meeting list, never hand-typed.
// ---------------------------------------------------------------------------

function countBy(metas: MeetingMeta[], pick: (meta: MeetingMeta) => string[]): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const meta of metas) {
    for (const value of pick(meta)) {
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }
  }
  return Array.from(counts.entries()).map(([name, count]) => ({ name, count }));
}

export const MOCK_FILTERS: Filters = {
  total: MOCK_METAS.length,
  tags: countBy(MOCK_METAS, (m) => m.tags),
  people: countBy(MOCK_METAS, (m) => m.people),
  features: { calendar: true, claude: true, settings: true },
};

// ---------------------------------------------------------------------------
// Search results (mock query, e.g. "sync").
// ---------------------------------------------------------------------------

export const MOCK_RESULTS: SearchResult[] = [
  {
    meetingId: standupDetail.id,
    title: standupDetail.title,
    dayLabel: standupDetail.dayLabel,
    time: standupDetail.time,
    kind: 'transcript',
    speaker: 'Sam',
    alsoSpeakers: [],
    seconds: 27,
    segmentIndex: 2,
    parts: [
      { text: 'One blocker on my end — offline ', hit: false },
      { text: 'sync', hit: true },
      { text: ' still drops on airplane mode.', hit: false },
    ],
  },
  {
    meetingId: sprintPlanningDetail.id,
    title: sprintPlanningDetail.title,
    dayLabel: sprintPlanningDetail.dayLabel,
    time: sprintPlanningDetail.time,
    kind: 'transcript',
    speaker: 'Alex',
    alsoSpeakers: ['Sam', 'Priya'],
    seconds: 10,
    segmentIndex: 0,
    parts: [
      { text: "Let's size the offline ", hit: false },
      { text: 'sync', hit: true },
      { text: ' fix first.', hit: false },
    ],
  },
  {
    meetingId: retroDetail.id,
    title: retroDetail.title,
    dayLabel: retroDetail.dayLabel,
    time: retroDetail.time,
    kind: 'notes',
    speaker: null,
    alsoSpeakers: [],
    seconds: null,
    segmentIndex: null,
    parts: [
      { text: 'The offline ', hit: false },
      { text: 'sync', hit: true },
      { text: ' bug took longer than planned.', hit: false },
    ],
  },
];

// ---------------------------------------------------------------------------
// Library upgrade status (variants keyed by name).
// ---------------------------------------------------------------------------

export const MOCK_STATUS: Record<'idle' | 'upgrading' | 'done' | 'doneWithSkips' | 'failed', LibraryStatus> = {
  idle: { state: 'idle', done: 0, total: 0, skipped: [] },
  upgrading: { state: 'upgrading', done: 42, total: 92, skipped: [] },
  done: { state: 'done', done: 92, total: 92, skipped: [] },
  doneWithSkips: {
    state: 'done',
    done: 89,
    total: 92,
    skipped: [
      { file: 'meeting-2026-03-04.json', reason: 'CorruptTranscript' },
      { file: 'meeting-2026-03-11.json', reason: 'UnknownSpeakerFormat' },
      { file: 'meeting-2026-04-02.json', reason: 'CorruptTranscript' },
    ],
  },
  failed: {
    state: 'failed',
    done: 12,
    total: 92,
    skipped: [{ file: '', reason: 'DiskReadError' }],
  },
};

// ---------------------------------------------------------------------------
// Today agenda (phase 3; 9b consumes this).
// ---------------------------------------------------------------------------

export const MOCK_AGENDA: AgendaEvent[] = [
  // End times for the two recorded rows match their meeting's own duration
  // (standupMeta/oneOnOneMeta above), so they're checkable against it.
  { key: 'a1', time: '9:00 AM', endTime: '9:12 AM', title: 'Stand-up', attendeeCount: 3, status: 'recorded', meetingId: standupDetail.id },
  { key: 'a2', time: '1:00 PM', endTime: '1:23 PM', title: 'Weekly 1:1 — Alex', attendeeCount: 2, status: 'recorded', meetingId: oneOnOneDetail.id },
  { key: 'a3', time: '3:30 PM', endTime: '4:30 PM', title: 'Design Sync', attendeeCount: 4, status: 'recording', meetingId: null },
  { key: 'a4', time: '4:30 PM', endTime: '5:00 PM', title: 'Roadmap Review', attendeeCount: 5, status: 'record', meetingId: null },
  { key: 'a5', time: '5:00 PM', endTime: '5:30 PM', title: 'Backlog Grooming', attendeeCount: 3, status: 'none', meetingId: null },
];

// ---------------------------------------------------------------------------
// Upcoming agenda (phase 3; next 7 days, collapsed by day). Days without
// events are omitted, as the spec asks.
// ---------------------------------------------------------------------------

export const MOCK_UPCOMING: { dayLabel: string; events: AgendaEvent[] }[] = [
  {
    dayLabel: 'Mon 28 Sep',
    events: [
      { key: 'u1', time: '9:00 AM', endTime: null, title: 'Stand-up', attendeeCount: 3, status: 'none', meetingId: null },
      { key: 'u2', time: '2:00 PM', endTime: null, title: 'Customer Call — Fenwick Labs', attendeeCount: 2, status: 'none', meetingId: null },
    ],
  },
  {
    dayLabel: 'Tue 29 Sep',
    events: [
      { key: 'u3', time: '9:00 AM', endTime: null, title: 'Stand-up', attendeeCount: 3, status: 'none', meetingId: null },
      { key: 'u4', time: '11:00 AM', endTime: null, title: 'Design Review — Checkout Flow', attendeeCount: 3, status: 'none', meetingId: null },
      { key: 'u5', time: '3:00 PM', endTime: null, title: 'Sprint Planning', attendeeCount: 4, status: 'none', meetingId: null },
    ],
  },
  {
    dayLabel: 'Thu 1 Oct',
    events: [
      { key: 'u6', time: '10:00 AM', endTime: null, title: 'Roadmap Review', attendeeCount: 5, status: 'none', meetingId: null },
    ],
  },
];

export interface ClaudeSetupInfo {
  command: string;
  desktopJson: string;
  executable: string;
  lastUsed: string | null;
  extensionAvailable: boolean;
  extensionNote: string | null;
}

export const MOCK_CLAUDE_SETUP: ClaudeSetupInfo = {
  command: 'claude mcp add speakeasy -- /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp',
  desktopJson: `{
  "mcpServers": {
    "speakeasy": {
      "command": "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy",
      "args": ["--mcp"]
    }
  }
}`,
  executable: '/Applications/Speakeasy.app/Contents/MacOS/Speakeasy',
  lastUsed: '2 min ago',
  extensionAvailable: true,
  extensionNote: null,
};
