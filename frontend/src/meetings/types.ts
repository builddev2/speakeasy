import type { ActionItem } from './actionItems.ts';

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
  startDate: string;
  time: string;
  duration: string;
  subtitle: string;
  speakerCount: number;
  hasSummary: boolean;
  approximate: boolean;
  tags: string[];
  people: string[];
}

export type CalendarAccess = 'connected' | 'denied' | 'unconnected';
export interface EventChip { key: string; title: string; time: string }
export interface UpcomingDay { dayLabel: string; events: AgendaEvent[] }
export interface CalendarToggle { id: string; name: string; enabled: boolean }
export interface CalendarAccountSettings { name: string; calendars: CalendarToggle[] }
export type Appearance = 'system' | 'light' | 'dark';
export interface MeetingSettings { offerToRecord: boolean; detectCalls: boolean; appearance: Appearance; identifyVoices: boolean; startAtLogin: boolean | null; accounts: CalendarAccountSettings[]; userName: string; userAliases: string[] }

export type SummaryBlock =
  | { kind: 'tldr'; text: string }
  | { kind: 'heading'; text: string }
  | { kind: 'para'; text: string }
  | { kind: 'bullets'; items: string[] };

/** Same shape as notesMarkdown.ts (which must stay import-free). */
export interface NotesValue { markdown: string; stamps: [number, number][] }

export interface RecordingInfo {
  recording: boolean; processing: boolean; startedAt: string | null; title: string | null;
  mode?: string; micFailure?: string | null; startError?: string | null; processingError?: string | null;
  pendingProcessing?: number;
}
export interface NotesDraft extends NotesValue { startedAt: string }

export interface MeetingDetail extends MeetingMeta {
  date: string;
  lines: TranscriptLine[];
  summary: string | null;
  summaryBlocks: SummaryBlock[];
  summaryQueued: boolean;
  actionItems: ActionItem[];
  event: EventChip | null;
  hasUserNotes: boolean;
  userNotes: NotesValue;
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
  kind: 'meeting' | 'transcript' | 'notes' | 'user_notes';
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
  /** ISO instants; the page compares these with the clock, not the display strings. */
  start: string;
  end: string | null;
}

export interface ClaudeSetupInfo {
  command: string;
  desktopJson: string;
  executable: string;
  lastUsed: string | null;
  extensionAvailable: boolean;
  extensionNote: string | null;
}
