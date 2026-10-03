import type { AgendaEvent, RecordingInfo } from '../mock/meetings';

export interface AgendaSplit {
  hero: AgendaEvent | null;
  heroKind: 'now' | 'next' | null;
  next: AgendaEvent[];
  more: AgendaEvent[];
  earlier: AgendaEvent[];
  earlierRecorded: number;
}
const VISIBLE_NEXT = 3;

function endMs(e: AgendaEvent): number {
  return e.end ? Date.parse(e.end) : Date.parse(e.start);
}

export function splitAgenda(agenda: AgendaEvent[], nowMs: number, recordingActive: boolean): AgendaSplit {
  const sorted = [...agenda].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const earlier = sorted.filter((e) => endMs(e) <= nowMs && e.status !== 'recording');
  const remaining = sorted.filter((e) => endMs(e) > nowMs && e.status !== 'recording');
  let hero: AgendaEvent | null = null;
  let heroKind: AgendaSplit['heroKind'] = null;
  if (!recordingActive) {
    hero = remaining.find((e) => Date.parse(e.start) <= nowMs && e.status !== 'recorded') ?? null;
    heroKind = hero ? 'now' : null;
    if (!hero) {
      hero = remaining.find((e) => Date.parse(e.start) > nowMs) ?? null;
      heroKind = hero ? 'next' : null;
    }
  }
  const later = remaining.filter((e) => e !== hero);
  return {
    hero,
    heroKind,
    next: later.slice(0, VISIBLE_NEXT),
    more: later.slice(VISIBLE_NEXT),
    earlier,
    earlierRecorded: earlier.filter((e) => e.status === 'recorded').length,
  };
}

export function heroWhen(e: AgendaEvent, nowMs: number): string {
  const start = Date.parse(e.start);
  if (start <= nowMs) return 'Now';
  const minutes = Math.max(1, Math.round((start - nowMs) / 60_000));
  return minutes < 60 ? `Starts in ${minutes} min` : `at ${e.time}`;
}

export function microphoneFailureText(reason?: string | null): string {
  if (reason === 'permission_blocked') return 'Allow Microphone in System Settings, then retry';
  if (reason === 'device_unavailable') return 'Connect a microphone or select an input, then retry';
  if (reason === 'teardown_pending') return 'Audio shutdown is still pending — restart Speakeasy';
  if (reason === 'helper_timeout') return 'Microphone did not respond — select Retry Microphone';
  return 'Microphone unavailable — select Retry Microphone';
}

export function engineBanner(rec: RecordingInfo): { text: string; action: 'retry' | null } | null {
  if (rec.mode === 'mic_failed') return { text: microphoneFailureText(rec.micFailure), action: 'retry' };
  if (rec.mode === 'ready' && rec.startError) {
    return {
      text: rec.startError === 'microphone_busy'
        ? 'Microphone is still being released. Wait, then try again.'
        : 'Meeting could not start. Restart Speakeasy, then try again.',
      action: null,
    };
  }
  if (rec.mode === 'ready' && rec.processingError) {
    return { text: 'Meeting could not finish normally. Check saved meetings before trying again.', action: null };
  }
  return null;
}
