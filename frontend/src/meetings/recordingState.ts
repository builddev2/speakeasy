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
