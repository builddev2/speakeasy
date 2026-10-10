import { useEffect, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import { formatDue, ownerDisplay } from './actionItems';
import type { ActionItem, Priority } from './actionItems';
import styles from './ActionItemRow.module.css';

export type ItemChange = Partial<Pick<ActionItem, 'task' | 'owner' | 'priority' | 'status' | 'notes' | 'tags'>>
  & { due?: string | null; dueReset?: boolean };

interface Props {
  item: ActionItem;
  userName: string;
  onChange: (change: ItemChange) => Promise<void>;
  onDelete: () => void;
  onOpenMeeting?: (meetingId: string) => void;
  showMeeting?: boolean;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (selected: boolean) => void;
  /** Controlled expansion: a row that changes group remounts, so the list owns this. */
  expanded?: boolean;
  onToggleExpanded?: () => void;
}

export function ActionItemRow({ item, userName, onChange, onDelete, onOpenMeeting, showMeeting = true,
  selectable = false, selected = false, onSelect, expanded, onToggleExpanded }: Props) {
  const [ownOpen, setOwnOpen] = useState(false);
  const open = expanded ?? ownOpen;
  const toggleOpen = () => (onToggleExpanded ? onToggleExpanded() : setOwnOpen(!ownOpen));
  const [error, setError] = useState(false);
  // Typed dates form valid values mid-keystroke, so the date commits on blur or Enter only.
  const [dueDraft, setDueDraft] = useState(item.due ?? '');
  useEffect(() => setDueDraft(item.due ?? ''), [item.due]);
  const [draft, setDraft] = useState({ task: item.task, owner: item.owner, notes: item.notes, tags: item.tags.join(', ') });
  // A refresh (e.g. Claude edited via MCP) updates fields the user isn't editing.
  useEffect(() => {
    if (!open) setDraft({ task: item.task, owner: item.owner, notes: item.notes, tags: item.tags.join(', ') });
  }, [item, open]);

  const save = (change: ItemChange) => {
    setError(false);
    onChange(change).catch(() => setError(true));
  };
  const commitText = (field: 'task' | 'owner' | 'notes') => {
    const value = field === 'notes' ? draft.notes : draft[field].trim();
    if (field === 'task' && !value) { setDraft({ ...draft, task: item.task }); return; }
    if (value !== item[field]) save({ [field]: value });
  };
  const commitDue = () => {
    if (dueDraft !== (item.due ?? '')) save({ due: dueDraft || null });
  };
  const commitTags = () => {
    const tags = draft.tags.split(',').map((t) => t.trim()).filter(Boolean);
    if (tags.join('\n') !== item.tags.join('\n')) save({ tags });
  };
  const enter = (fn: () => void) => (e: KeyboardEvent) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); fn(); } };
  const done = item.status === 'done';

  return (
    <li className={`${styles.row} ${done ? styles.done : ''}`}>
      <div className={styles.line}>
        {selectable && (
          <input type="checkbox" className={styles.select} aria-label="Select item"
            checked={selected} onChange={(e) => onSelect?.(e.target.checked)} />
        )}
        <button type="button" role="checkbox" aria-checked={done} aria-label={done ? 'Mark as open' : 'Mark as done'}
          className={`${styles.check} ${done ? styles.checkOn : ''}`}
          onClick={() => save({ status: done ? 'open' : 'done' })}>{done ? '✓' : ''}</button>
        <button type="button" className={styles.main} aria-expanded={open} onClick={toggleOpen}>
          <span className={styles.task}>{item.task}</span>
          <span className={styles.meta}>
            {item.priority !== 'normal' && <span className={`${styles.priority} ${styles[item.priority]}`}>{item.priority}</span>}
            <span className={styles.owner}>{ownerDisplay(item, userName)}</span>
            {item.due && (
              <span className={styles.due}>
                {formatDue(item.due)}
                {item.duePhrase && <span className={styles.phrase}> “{item.duePhrase}”</span>}
                {item.dueSource === 'user' && <span className={styles.edited}> edited</span>}
              </span>
            )}
            {!item.due && item.duePhrase && (
              <span className={styles.due}><span className={styles.phrase}>“{item.duePhrase}”</span></span>
            )}
            {item.meetingTags.map((t) => <span key={`m-${t}`} className={styles.tagMuted}>{t}</span>)}
            {item.tags.map((t) => <span key={`o-${t}`} className={styles.tag}>{t}</span>)}
            {item.notes && <span className={styles.hasNotes} aria-label="Has notes">✎</span>}
          </span>
        </button>
        {showMeeting && (item.meetingId && onOpenMeeting ? (
          <button type="button" className={styles.meeting} onClick={() => onOpenMeeting(item.meetingId!)}>
            {item.meetingTitle} · {item.meetingDate}
          </button>
        ) : !item.meetingId && item.source === 'summary' ? (
          <span className={styles.meetingGone}>Meeting deleted</span>
        ) : null)}
      </div>
      {open && (
        <div className={styles.editor}>
          <label>Task<input {...NO_AUTOCORRECT} value={draft.task} maxLength={500}
            onChange={(e) => setDraft({ ...draft, task: e.target.value })} onBlur={() => commitText('task')} onKeyDown={enter(() => commitText('task'))} /></label>
          <label>Owner<input {...NO_AUTOCORRECT} value={draft.owner} maxLength={80} placeholder="Unassigned"
            onChange={(e) => setDraft({ ...draft, owner: e.target.value })} onBlur={() => commitText('owner')} onKeyDown={enter(() => commitText('owner'))} /></label>
          <label>Priority<select value={item.priority} onChange={(e) => save({ priority: e.target.value as Priority })}>
            <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
          </select></label>
          <label>Due<input type="date" value={dueDraft}
            onChange={(e) => setDueDraft(e.target.value)} onBlur={commitDue} onKeyDown={enter(commitDue)} /></label>
          <div className={styles.dueActions}>
            <button type="button" onClick={() => save({ due: null })} disabled={item.due === null}>No date</button>
            {item.claudeDue !== null || item.dueSource === 'user' ? (
              <button type="button" onClick={() => save({ dueReset: true })} disabled={item.dueSource !== 'user'}>
                Reset to Claude’s{item.claudeDue ? ` (${formatDue(item.claudeDue)})` : ''}
              </button>
            ) : null}
          </div>
          <label>Tags<input {...NO_AUTOCORRECT} value={draft.tags} placeholder="Comma-separated"
            onChange={(e) => setDraft({ ...draft, tags: e.target.value })} onBlur={commitTags} onKeyDown={enter(commitTags)} /></label>
          <label className={styles.notes}>Notes<textarea value={draft.notes} maxLength={5000} rows={3}
            onChange={(e) => setDraft({ ...draft, notes: e.target.value })} onBlur={() => commitText('notes')} /></label>
          <button type="button" className={styles.delete} onClick={onDelete}>Delete item</button>
        </div>
      )}
      {error && <div className={styles.error} role="status">Couldn’t save — try again.</div>}
    </li>
  );
}
