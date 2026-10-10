import { useEffect, useMemo, useState } from 'react';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import { ActionItemRow } from './ActionItemRow';
import type { ItemChange } from './ActionItemRow';
import { DEFAULT_VIEW, effectiveTags, filterItems, groupItems, ownersOf, parseViewState, resolveOwnerName } from './actionItems';
import type { ActionItem, GroupBy, OwnerFilter, Priority, SortBy, ViewState } from './actionItems';
import styles from './ActionItemsView.module.css';

const VIEW_KEY = 'actions.view.v1';
function readView(): ViewState { try { return parseViewState(window.localStorage.getItem(VIEW_KEY)); } catch { return DEFAULT_VIEW; } }
function writeView(v: ViewState) { try { window.localStorage.setItem(VIEW_KEY, JSON.stringify(v)); } catch { /* private mode */ } }

interface Props {
  items: ActionItem[]; identitySet: boolean; today: string; userName: string;
  onUpdate: (id: number, change: ItemChange) => Promise<void>;
  onCreate: (fields: { task: string; owner: string; due: string | null; priority: Priority }) => Promise<void>;
  onDelete: (id: number) => void;
  onBulk: (ids: number[], change: { status?: 'open' | 'done'; addTags?: string[]; delete?: boolean }) => Promise<void>;
  onOpenMeeting: (meetingId: string) => void;
  onOpenSettings: () => void;
  loadError?: boolean;
}

const ownerValue = (o: OwnerFilter) => (o.kind === 'person' ? `person:${o.name}` : o.kind);
const parseOwner = (v: string): OwnerFilter =>
  v.startsWith('person:') ? { kind: 'person', name: v.slice(7) } : v === 'all' ? { kind: 'all' } : { kind: 'mine' };

const GROUPS: [GroupBy, string][] = [['due', 'Due'], ['meeting', 'Meeting'], ['owner', 'Owner'], ['tag', 'Tag'], ['none', 'None']];
const SORTS: [SortBy, string][] = [['due', 'Due'], ['priority', 'Priority'], ['meeting', 'Meeting date'], ['created', 'Created']];
const STATUSES: ['open' | 'done' | 'all', string][] = [['open', 'Open'], ['done', 'Completed'], ['all', 'All']];
const PRIORITIES: [Priority, string][] = [['high', 'High'], ['normal', 'Normal'], ['low', 'Low']];

export function ActionItemsView(props: Props) {
  const { items, identitySet, today, userName } = props;
  const [view, setView] = useState<ViewState>(readView);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkMode, setBulkMode] = useState(false);
  // Groups the user flipped from their default. Completed defaults to collapsed only when status is All.
  const [toggled, setToggled] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ task: '', owner: userName, due: '', priority: 'normal' as Priority });
  const [formError, setFormError] = useState('');
  const [bulkError, setBulkError] = useState('');
  const [addingTag, setAddingTag] = useState(false);
  const [tagDraft, setTagDraft] = useState('');
  useEffect(() => writeView(view), [view]);
  useEffect(() => setSelected((s) => new Set([...s].filter((id) => items.some((i) => i.id === id)))), [items]);

  const update = (patch: Partial<ViewState>) => setView((v) => ({ ...v, ...patch }));
  const allTags = useMemo(() => [...new Set(items.flatMap(effectiveTags))].sort((a, b) => a.localeCompare(b)), [items]);
  const owners = useMemo(() => ownersOf(items, userName), [items, userName]);
  const effView = useMemo<ViewState>(() => view.owner.kind === 'person'
    ? { ...view, owner: { kind: 'person', name: resolveOwnerName(view.owner.name, owners, userName, items) } } : view,
  [view, owners, userName, items]);
  const visible = useMemo(() => filterItems(items, effView, identitySet, userName), [items, effView, identitySet, userName]);
  const groups = useMemo(() => groupItems(visible, view, today, userName), [visible, view, today, userName]);

  const isCollapsed = (key: string) => (key === 'done' && view.status === 'all') !== toggled.has(key);
  const toggleGroup = (key: string) => setToggled((t) => { const n = new Set(t); if (n.has(key)) n.delete(key); else n.add(key); return n; });
  const filtersActive = view.owner.kind !== 'mine' || view.tags.length > 0 || view.priorities.length > 0
    || view.status !== 'open' || view.query !== '';
  const ownerName = effView.owner.kind === 'person' ? effView.owner.name : null;
  const ownerOptions = ownerName && !owners.includes(ownerName) ? [...owners, ownerName] : owners;
  const toggleTag = (t: string) => update({ tags: view.tags.includes(t) ? view.tags.filter((x) => x !== t) : [...view.tags, t] });
  const togglePriority = (p: Priority) =>
    update({ priorities: view.priorities.includes(p) ? view.priorities.filter((x) => x !== p) : [...view.priorities, p] });

  const submitAdd = () => {
    const task = draft.task.trim();
    if (!task) return;
    setFormError('');
    props.onCreate({ task, owner: draft.owner.trim(), due: draft.due || null, priority: draft.priority })
      .then(() => { setDraft({ task: '', owner: userName, due: '', priority: 'normal' }); setAdding(false); })
      .catch((err) => { console.error('create action item failed', err); setFormError('Couldn’t add the item — try again.'); });
  };
  const bulk = (change: Parameters<Props['onBulk']>[1]) => {
    const ids = [...selected];
    setBulkError('');
    props.onBulk(ids, change)
      .then(() => { setSelected(new Set()); setAddingTag(false); setTagDraft(''); })
      .catch((err) => { console.error('bulk action failed', err); setBulkError('Couldn’t update the selected items — try again.'); });
  };
  const submitTag = () => {
    const tag = tagDraft.trim();
    if (tag) bulk({ addTags: [tag] });
  };

  return (
    <div className={styles.view}>
      <div className={styles.header}>
        <h1 className={styles.title}>Action items</h1>
        <div className={styles.headerActions}>
          <button type="button" className={styles.button} aria-pressed={bulkMode}
            onClick={() => { setBulkMode(!bulkMode); setSelected(new Set()); }}>{bulkMode ? 'Done selecting' : 'Select'}</button>
          <button type="button" className={styles.button} onClick={() => setAdding(true)}>+ Add item</button>
        </div>
      </div>

      <div className={styles.toolbar}>
        <select className={styles.control} aria-label="Owner" value={ownerValue(effView.owner)}
          onChange={(e) => update({ owner: parseOwner(e.target.value) })}>
          <option value="mine">Mine</option>
          <option value="all">Everyone</option>
          {ownerOptions.map((o) => <option key={o} value={`person:${o}`}>{o}</option>)}
        </select>
        <div className={styles.segmented} role="radiogroup" aria-label="Status">
          {STATUSES.map(([value, label]) => (
            <button key={value} type="button" role="radio" aria-checked={view.status === value}
              className={view.status === value ? `${styles.segment} ${styles.segmentOn}` : styles.segment}
              onClick={() => update({ status: value })}>{label}</button>
          ))}
        </div>
        <div className={styles.chips} role="group" aria-label="Priority">
          {PRIORITIES.map(([value, label]) => (
            <button key={value} type="button" aria-pressed={view.priorities.includes(value)}
              className={view.priorities.includes(value) ? `${styles.chip} ${styles.chipOn}` : styles.chip}
              onClick={() => togglePriority(value)}>{label}</button>
          ))}
        </div>
        <details className={styles.tagMenu}>
          <summary className={styles.control}>{view.tags.length ? `Tags · ${view.tags.length}` : 'Tags'}</summary>
          <div className={styles.tagList}>
            {allTags.length === 0 && <span className={styles.muted}>No tags yet</span>}
            {allTags.map((t) => (
              <label key={t}><input type="checkbox" checked={view.tags.includes(t)} onChange={() => toggleTag(t)} />{t}</label>
            ))}
          </div>
        </details>
        <input {...NO_AUTOCORRECT} type="search" className={`${styles.control} ${styles.search}`} placeholder="Search"
          aria-label="Search action items" value={view.query} onChange={(e) => update({ query: e.target.value })} />
        <label className={styles.inline}>Group
          <select className={styles.control} value={view.groupBy} onChange={(e) => update({ groupBy: e.target.value as GroupBy })}>
            {GROUPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className={styles.inline}>Sort
          <select className={styles.control} value={view.sortBy} onChange={(e) => update({ sortBy: e.target.value as SortBy })}>
            {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {filtersActive && (
          <button type="button" className={styles.link}
            onClick={() => update({ owner: DEFAULT_VIEW.owner, tags: [], priorities: [], status: DEFAULT_VIEW.status, query: '' })}>
            Clear filters
          </button>
        )}
      </div>

      {props.loadError && <div className={styles.errorNote} role="status">Couldn’t load action items. They’ll refresh on the next change.</div>}

      {view.owner.kind === 'mine' && !identitySet && (
        <div className={styles.banner} role="status">
          <span>Set your name in Settings to see your items. <span className={styles.muted}>Showing everyone’s.</span></span>
          <button type="button" className={styles.button} onClick={props.onOpenSettings}>Open Settings</button>
        </div>
      )}

      {adding && (
        <div className={styles.addRow}>
          <input {...NO_AUTOCORRECT} autoFocus className={`${styles.control} ${styles.addTask}`} placeholder="Task" aria-label="New task"
            maxLength={500} value={draft.task} onChange={(e) => setDraft({ ...draft, task: e.target.value })}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submitAdd(); } }} />
          <input {...NO_AUTOCORRECT} className={styles.control} placeholder="Owner" aria-label="New owner" maxLength={80}
            value={draft.owner} onChange={(e) => setDraft({ ...draft, owner: e.target.value })} />
          <input type="date" className={styles.control} aria-label="New due date" value={draft.due}
            onChange={(e) => setDraft({ ...draft, due: e.target.value })} />
          <select className={styles.control} aria-label="New priority" value={draft.priority}
            onChange={(e) => setDraft({ ...draft, priority: e.target.value as Priority })}>
            <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
          </select>
          <button type="button" className={styles.button} onClick={submitAdd}>Add</button>
          <button type="button" className={styles.button} onClick={() => setAdding(false)}>Cancel</button>
          {formError && <span className={styles.errorText} role="status">{formError}</span>}
        </div>
      )}

      {bulkMode && selected.size > 0 && (
        <div className={styles.bulkBar}>
          <span>{selected.size} selected</span>
          <button type="button" className={styles.button} onClick={() => bulk({ status: 'done' })}>Complete</button>
          <button type="button" className={styles.button} onClick={() => bulk({ status: 'open' })}>Reopen</button>
          {addingTag ? (
            <input {...NO_AUTOCORRECT} autoFocus className={styles.control} placeholder="Tag, then Enter" aria-label="Tag to add"
              value={tagDraft} onChange={(e) => setTagDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submitTag(); } else if (e.key === 'Escape') setAddingTag(false); }} />
          ) : (
            <button type="button" className={styles.button} onClick={() => setAddingTag(true)}>Add tag…</button>
          )}
          <button type="button" className={`${styles.button} ${styles.danger}`} onClick={() => bulk({ delete: true })}>Delete</button>
          {bulkError && <span className={styles.errorText} role="status">{bulkError}</span>}
        </div>
      )}

      <div className={styles.body}>
        {visible.length === 0 ? (
          <div className={styles.empty}>{filtersActive ? 'No items match these filters.' : 'Nothing due — nice.'}</div>
        ) : groups.map((g) => {
          const collapsed = view.groupBy !== 'none' && isCollapsed(g.key);
          return (
            <section key={g.key} className={styles.group}>
              {view.groupBy !== 'none' && (
                <button type="button" aria-expanded={!collapsed} onClick={() => toggleGroup(g.key)}
                  className={`${styles.groupHeading} ${g.tone === 'overdue' ? styles.overdue : ''}`}>
                  <span className={collapsed ? styles.chevron : `${styles.chevron} ${styles.chevronOpen}`} aria-hidden="true">›</span>
                  {g.label} <span className={styles.groupCount}>{g.items.length}</span>
                </button>
              )}
              {!collapsed && (
                <ul className={styles.list}>
                  {g.items.map((item) => (
                    <ActionItemRow key={item.id} userName={userName} item={item}
                      onChange={(c) => props.onUpdate(item.id, c)}
                      onDelete={() => props.onDelete(item.id)}
                      onOpenMeeting={props.onOpenMeeting}
                      expanded={openId === item.id}
                      onToggleExpanded={() => setOpenId(openId === item.id ? null : item.id)}
                      selectable={bulkMode}
                      selected={selected.has(item.id)}
                      onSelect={(on) => setSelected((s) => { const n = new Set(s); if (on) n.add(item.id); else n.delete(item.id); return n; })} />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
