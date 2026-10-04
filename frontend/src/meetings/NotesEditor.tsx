import { useEffect, useRef, useState, type MutableRefObject, type ReactNode } from 'react';
import { EditorContent, useEditor, useEditorState } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { TaskItem, TaskList } from '@tiptap/extension-list';
import { docToNotes, notesToDoc, type NotesValue } from './notesMarkdown';
import { Stamps } from './notesStamps';
import styles from './NotesEditor.module.css';

const SAVE_DELAY_MS = 500;
const RETRY_MS = 5000;
type Status = 'idle' | 'saving' | 'saved' | 'error' | 'tooLong';

interface NotesEditorProps {
  initial: NotesValue;
  save: (value: NotesValue) => Promise<void>;
  notice?: ReactNode;
  /** Set to "take the final value": cancels pending/retry saves, blocks further saves, returns the text. */
  valueRef?: MutableRefObject<(() => NotesValue) | null>;
}

export function NotesEditor({ initial, save, notice, valueRef }: NotesEditorProps) {
  const [status, setStatus] = useState<Status>('idle');
  const timer = useRef<number | null>(null);
  const pending = useRef<NotesValue | null>(null);
  const closed = useRef(false);
  const saveRef = useRef(save);
  saveRef.current = save;

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2] },
        blockquote: false, code: false, codeBlock: false, horizontalRule: false,
        strike: false, underline: false, link: false, hardBreak: false,
      }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Stamps,
    ],
    content: notesToDoc(initial),
    editorProps: { attributes: { class: styles.prose, 'aria-label': 'Notes' } },
    onUpdate: ({ editor: e }) => schedule(docToNotes(e.getJSON())),
  });

  const flush = () => {
    if (closed.current) return;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    const value = pending.current;
    if (value === null) return;
    setStatus('saving');
    saveRef.current(value).then(
      () => { if (pending.current === value) { pending.current = null; setStatus('saved'); } },
      (err: Error) => {
        if (String(err?.message).includes('Notes are too long to save')) { setStatus('tooLong'); return; }
        setStatus('error');
        timer.current = window.setTimeout(flush, RETRY_MS);
      },
    );
  };

  function schedule(value: NotesValue) {
    if (closed.current) return;
    pending.current = value;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, SAVE_DELAY_MS);
  }

  useEffect(() => {
    const onHide = () => flush();
    window.addEventListener('pagehide', onHide);
    return () => { window.removeEventListener('pagehide', onHide); flush(); };
  }, []);

  useEffect(() => {
    if (!valueRef || !editor) return;
    const take = (): NotesValue => {
      closed.current = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      pending.current = null;
      return docToNotes(editor.getJSON());
    };
    valueRef.current = take;
    return () => { if (valueRef.current === take) valueRef.current = null; };
  }, [editor, valueRef]);

  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e?.isActive('bold') ?? false, italic: e?.isActive('italic') ?? false,
      h1: e?.isActive('heading', { level: 1 }) ?? false, h2: e?.isActive('heading', { level: 2 }) ?? false,
      bullet: e?.isActive('bulletList') ?? false, ordered: e?.isActive('orderedList') ?? false,
      task: e?.isActive('taskList') ?? false,
    }),
  });

  const label = { idle: '', saving: 'Saving…', saved: 'Saved', error: 'Couldn\'t save — retrying',
    tooLong: 'Notes are too long to save' }[status];
  const button = (key: keyof NonNullable<typeof active>, title: string, run: () => void, body: ReactNode) => (
    <button type="button" className={active?.[key] ? `${styles.tool} ${styles.toolOn}` : styles.tool}
      aria-label={title} title={title} aria-pressed={active?.[key] ?? false}
      onMouseDown={(e) => e.preventDefault()} onClick={run}>{body}</button>
  );
  const chain = () => editor!.chain().focus();

  return (
    <div className={styles.notes}>
      <div className={styles.bar} role="toolbar" aria-label="Formatting">
        {button('bold', 'Bold', () => chain().toggleBold().run(), <b>B</b>)}
        {button('italic', 'Italic', () => chain().toggleItalic().run(), <i>I</i>)}
        {button('h1', 'Heading 1', () => chain().toggleHeading({ level: 1 }).run(), 'H1')}
        {button('h2', 'Heading 2', () => chain().toggleHeading({ level: 2 }).run(), 'H2')}
        {button('bullet', 'Bulleted list', () => chain().toggleBulletList().run(),
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4h7.5M6 8h7.5M6 12h7.5"/><circle cx="3" cy="4" r="0.9"/><circle cx="3" cy="8" r="0.9"/><circle cx="3" cy="12" r="0.9"/></svg>)}
        {button('ordered', 'Numbered list', () => chain().toggleOrderedList().run(),
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 4h7M6.5 8h7M6.5 12h7M2.5 3l1-.5V5.5M2.3 7.3c.4-.6 1.7-.5 1.7.3 0 .7-1.7 1.4-1.7 2.3H4"/></svg>)}
        {button('task', 'Checklist', () => chain().toggleTaskList().run(),
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2.5" width="4" height="4" rx="1"/><path d="M8.5 4.5h5M8.5 11.5h5"/><rect x="2" y="9.5" width="4" height="4" rx="1"/><path d="M2.8 11.4l.9.9 1.6-1.8"/></svg>)}
        <span className={styles.spacer} />
        <span className={status === 'error' || status === 'tooLong' ? styles.statusError : styles.status} role="status">{label}</span>
      </div>
      {notice}
      <EditorContent editor={editor} />
    </div>
  );
}
