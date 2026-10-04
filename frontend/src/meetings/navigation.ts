import type { RecordingInfo } from '../mock/meetings';

export type Navigation = 'recording' | 'settings' | { meeting: string } | null;
export type NavigationAction =
  | { kind: 'recording' } | { kind: 'today' } | { kind: 'settings' }
  | { kind: 'meeting'; id: string } | { kind: 'none' };

export function navigationAction(nav: Navigation, recording: RecordingInfo): NavigationAction {
  if (nav === 'recording') return recording.recording || recording.processing ? { kind: 'recording' } : { kind: 'today' };
  if (nav === 'settings') return { kind: 'settings' };
  if (nav && typeof nav === 'object' && typeof nav.meeting === 'string' && nav.meeting) return { kind: 'meeting', id: nav.meeting };
  return { kind: 'none' };
}
