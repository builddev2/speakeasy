export interface TranscriptLine {
  time: string;
  speakerNumber: number;
  speakerLabel: string;
  text: string;
  segmentIndex: number;
  confidence: number | null;
  overlap: boolean;
}

export interface MeetingMeta {
  id: string;
  title: string;
  subtitle: string;
  date: string;
  duration: string;
  speakerCount: number;
}

export interface MeetingDetail extends MeetingMeta {
  lines: TranscriptLine[];
}

/** @deprecated kept as alias for phase-1 call sites */
export type Meeting = MeetingDetail;

export const SPEAKER_PALETTE = [
  '#6fd8b0',
  '#F0B34A',
  '#C08CF2',
  '#6EA8FF',
  '#7ED97E',
  '#F0895A',
  '#DE8FB4',
];

export function speakerColor(speakerNumber: number, colorCodeSpeakers: boolean): string {
  if (!colorCodeSpeakers) return 'rgba(255,255,255,0.55)';
  return SPEAKER_PALETTE[(speakerNumber - 1) % SPEAKER_PALETTE.length];
}

function line(time: string, speakerNumber: number, text: string): TranscriptLine {
  return { time: `[${time}]`, speakerNumber, speakerLabel: `Speaker ${speakerNumber}`, text, segmentIndex: 0, confidence: null, overlap: false };
}

export const MEETINGS: MeetingDetail[] = [
  {
    id: 'meeting-1',
    title: 'Meeting — Jul 8, 7:43 PM',
    subtitle: '5 min · 17 speakers',
    date: 'Jul 8, 2026 · 19:43',
    duration: '5 min',
    speakerCount: 17,
    lines: [
      line('00:00:00', 1, "Okay, are we recording? Yep — we're live."),
      line('00:00:15', 2, "Great. Let's start with last week's numbers."),
      line('00:00:27', 3, "Revenue's up nine percent over the quarter."),
      line('00:00:36', 4, 'When you finish, walk me through the churn cohort.'),
      line('00:00:42', 5, 'Sure — give me one second to pull it up.'),
      line('00:00:55', 6, 'While that loads — any blockers on the mobile build?'),
      line('00:01:03', 1, 'One. Offline sync still drops on airplane mode.'),
      line('00:01:10', 2, "Let's file that as a P1 and keep moving."),
      line('00:01:21', 7, "Agreed — I'll take it right after standup."),
      line('00:02:31', 3, "Circling back — the cohort's holding at ninety-four percent."),
    ],
  },
  {
    id: 'meeting-2',
    title: 'Test Meeting — Jul 8, 6:56 PM',
    subtitle: '2 min · 3 speakers',
    date: 'Jul 8, 2026 · 18:56',
    duration: '2 min',
    speakerCount: 3,
    lines: [
      line('00:00:00', 1, 'Quick sync — can everyone hear me okay?'),
      line('00:00:08', 2, 'Loud and clear.'),
      line('00:00:14', 3, "Same here, let's keep this short."),
    ],
  },
  {
    id: 'meeting-3',
    title: 'Test Meeting — Jul 8, 6:53 PM',
    subtitle: '1 min · 2 speakers',
    date: 'Jul 8, 2026 · 18:53',
    duration: '1 min',
    speakerCount: 2,
    lines: [
      line('00:00:00', 1, 'Testing the recorder before the real call.'),
      line('00:00:06', 2, 'Sounds good on my end.'),
    ],
  },
];
