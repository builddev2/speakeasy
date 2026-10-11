import type { RecordingInfo } from '../mock/meetings.ts';

export interface RecordingTrack { active: boolean; startedAt: string | null; firstSeen: string | null }

/**
 * Works out the recording's start time. The engine keeps its last start time
 * after a meeting ends, so `info.startedAt` counts only while recording or
 * processing; when it is null we use the moment the page first saw activity.
 */
export function trackRecording(info: RecordingInfo, firstSeen: string | null, nowIso: string): RecordingTrack {
  if (!info.recording && !info.processing) return { active: false, startedAt: null, firstSeen: null };
  const seen = firstSeen ?? nowIso;
  return { active: true, startedAt: info.startedAt ?? seen, firstSeen: seen };
}

/** A saved meeting takes the open recording notes only if they were typed for it. */
export function shouldHandOverNotes(
  recordingView: boolean, savedStartedAt: string | null | undefined, recordingStartedAt: string | null,
): boolean {
  if (!recordingView) return false;
  if (!savedStartedAt || !recordingStartedAt) return true;
  return Date.parse(savedStartedAt) === Date.parse(recordingStartedAt);
}

/** Earlier meetings may still be processing; only a live recording blocks a new one. */
export function canStartRecording(info: RecordingInfo): boolean {
  return !info.recording;
}

export function processingNote(info: RecordingInfo): string | null {
  const n = info.pendingProcessing ?? 0;
  if (!info.recording || n < 1) return null;
  return n === 1 ? 'Processing 1 earlier meeting' : `Processing ${n} earlier meetings`;
}
