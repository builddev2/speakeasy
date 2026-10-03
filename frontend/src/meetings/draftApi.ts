import { bridge } from '../bridge';
import type { NotesDraft } from '../mock/meetings';

// In the browser mock there is no bridge; the draft lives here.
let mockDraft: NotesDraft | null = null;

export const draftApi = {
  seedMock(draft: NotesDraft | null) { mockDraft = draft; },
  get(): Promise<NotesDraft | null> {
    return bridge.embedded ? bridge.call<NotesDraft | null>('notes.draft.get') : Promise.resolve(mockDraft);
  },
  set(draft: NotesDraft): Promise<void> {
    if (bridge.embedded) return bridge.call('notes.draft.set', { ...draft }).then(() => undefined);
    mockDraft = draft;
    return Promise.resolve();
  },
  discard(): Promise<void> {
    if (bridge.embedded) return bridge.call('notes.draft.discard').then(() => undefined);
    mockDraft = null;
    return Promise.resolve();
  },
  finish(payload: NotesDraft & { id: string }): Promise<void> {
    if (bridge.embedded) return bridge.call('notes.draft.finish', { ...payload }).then(() => undefined);
    console.log('notes.draft.finish', payload);
    mockDraft = null;
    return Promise.resolve();
  },
};
