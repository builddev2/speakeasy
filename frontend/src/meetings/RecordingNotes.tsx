import { useEffect, useRef, useState } from 'react';
import type { MutableRefObject } from 'react';
import type { RecordingInfo } from '../mock/meetings';
import { NotesEditor } from './NotesEditor';
import { docToNotes, notesToDoc, stripStamps } from './notesMarkdown';
import type { NotesValue } from './notesMarkdown';
import { draftApi } from './draftApi';
import styles from './RecordingNotes.module.css';

interface RecordingNotesProps {
  info: RecordingInfo;
  startedAt: string;
  editorRef: MutableRefObject<(() => NotesValue) | null>;
}

interface Loaded { initial: NotesValue; leftover: boolean }

export function RecordingNotes({ info, startedAt, editorRef }: RecordingNotesProps) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [draftKey, setDraftKey] = useState(0);
  // NotesEditor captures `now` once, so it reads the start time through a ref.
  const startedAtRef = useRef(startedAt);
  startedAtRef.current = startedAt;
  const startedAtLoad = useRef(startedAt);

  useEffect(() => {
    let cancelled = false;
    draftApi.get().then((draft) => {
      if (cancelled) return;
      if (!draft) { setLoaded({ initial: { markdown: '', stamps: [] }, leftover: false }); return; }
      if (draft.startedAt !== startedAtLoad.current) {
        // Left over from an unsaved recording: its stamps point at audio that was never saved.
        const initial = docToNotes(stripStamps(notesToDoc({ markdown: draft.markdown, stamps: draft.stamps })));
        setLoaded({ initial, leftover: true });
        return;
      }
      setLoaded({ initial: { markdown: draft.markdown, stamps: draft.stamps }, leftover: false });
    }).catch((err) => {
      console.error('notes.draft.get failed', err);
      if (!cancelled) setLoaded({ initial: { markdown: '', stamps: [] }, leftover: false });
    });
    return () => { cancelled = true; };
  }, []);

  function discard() {
    // Take the editor's final value first so no pending autosave can recreate the draft.
    editorRef.current?.();
    draftApi.discard()
      .catch((err) => console.error('notes.draft.discard failed', err))
      .finally(() => {
        setLoaded({ initial: { markdown: '', stamps: [] }, leftover: false });
        setDraftKey((k) => k + 1);
      });
  }

  const now = () => {
    const seconds = (Date.now() - Date.parse(startedAtRef.current)) / 1000;
    return Number.isFinite(seconds) ? seconds : null;
  };

  return (
    <div className={styles.pane}>
      <div className={styles.body}>
        <div className={styles.bodyInner}>
          <div className={styles.title}>{info.title ?? 'Meeting in progress'}</div>
          {loaded && (
            <NotesEditor
              key={draftKey}
              initial={loaded.initial}
              now={now}
              save={(v) => draftApi.set({ ...v, startedAt: startedAtRef.current })}
              valueRef={editorRef}
              notice={loaded.leftover ? (
                <div className={styles.notice}>
                  <span className={styles.noticeText}>Notes from a recording that wasn't saved</span>
                  <button type="button" className={styles.noticeButton} onClick={discard}>Discard</button>
                </div>
              ) : undefined}
            />
          )}
        </div>
      </div>
    </div>
  );
}
