import type { TranscriptLine } from '../mock/meetings.ts';

export interface Turn {
  speakerNumber: number;
  speakerLabel: string;
  start: number;
  time: string;
  lines: TranscriptLine[];
}

/** "0:03", "12:40", "1:02:15" — elapsed since the meeting started. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** Consecutive lines with the same speaker number and label become one turn. */
export function groupTurns(lines: TranscriptLine[]): Turn[] {
  const turns: Turn[] = [];
  for (const line of lines) {
    const last = turns[turns.length - 1];
    if (last && last.speakerNumber === line.speakerNumber && last.speakerLabel === line.speakerLabel) {
      last.lines.push(line);
    } else {
      turns.push({ speakerNumber: line.speakerNumber, speakerLabel: line.speakerLabel,
        start: line.start, time: line.time, lines: [line] });
    }
  }
  return turns;
}
