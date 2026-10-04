import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { MeetingMeta } from '../mock/meetings';
import { buildSections, isOpen, parseOpenState, sectionPathFor, visibleRows } from './listSections';
import type { OpenState, Section } from './listSections';
import styles from './MeetingList.module.css';

const STORAGE_KEY = 'meetingList.sections';

function readState(): OpenState {
  try {
    return parseOpenState(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return {};
  }
}

function writeState(state: OpenState) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable: the state just isn't remembered */
  }
}

function findSection(sections: Section[], key: string): Section | null {
  for (const s of sections) {
    if (s.key === key) return s;
    const inner = findSection(s.children, key);
    if (inner) return inner;
  }
  return null;
}

interface MeetingListProps {
  metas: MeetingMeta[];
  selectedId: string | null;
  /** Local 'YYYY-MM-DD'; sections regroup when it changes. */
  today: string;
  /** A Tag/Person filter is active: show every section open, store nothing. */
  expandAll: boolean;
  onSelect: (id: string) => void;
  onRequestSearchFocus?: () => void;
  onRequestDelete?: () => void;
}

function rowId(id: string): string {
  return `meeting-row-${id}`;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;
}

export function MeetingList({
  metas,
  selectedId,
  today,
  expandAll,
  onSelect,
  onRequestSearchFocus,
  onRequestDelete,
}: MeetingListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const pendingScroll = useRef(false);
  const [openState, setOpenState] = useState<OpenState>(readState);
  const sections = useMemo(() => buildSections(metas, today), [metas, today]);
  const rows = useMemo(() => visibleRows(sections, openState, expandAll), [sections, openState, expandAll]);

  function setOpen(keys: string[], open: boolean) {
    setOpenState((prev) => {
      const next = { ...prev };
      for (const k of keys) next[k] = open;
      writeState(next);
      return next;
    });
  }

  const selectedPath = useMemo(
    () => (selectedId ? sectionPathFor(sections, selectedId).join('/') : ''),
    [sections, selectedId],
  );

  // A selection made elsewhere (search, Today, after a delete) may sit in a
  // collapsed section: open its path, then scroll once the row exists.
  useEffect(() => {
    if (!selectedId) return;
    pendingScroll.current = true;
    if (expandAll) return;
    const closed = sectionPathFor(sections, selectedId).filter((key) => {
      const s = findSection(sections, key);
      return s !== null && !isOpen(s, openState, false);
    });
    if (closed.length) setOpen(closed, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on a new selection, a filter change, or when the selected row's section path changes (e.g. it first appears, or midnight moves it)
  }, [selectedId, expandAll, selectedPath]);

  useEffect(() => {
    if (!pendingScroll.current || !selectedId) return;
    const el = rowRefs.current.get(selectedId);
    if (el) {
      el.scrollIntoView({ block: 'nearest' });
      pendingScroll.current = false;
    }
  }, [selectedId, rows]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (isEditableTarget(e.target)) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
      e.preventDefault();
      onRequestSearchFocus?.();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Backspace') {
      if (selectedId) {
        e.preventDefault();
        onRequestDelete?.();
      }
      return;
    }
    if (rows.length === 0) return;
    const index = rows.findIndex((m) => m.id === selectedId);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = index < 0 ? 0 : Math.min(index + 1, rows.length - 1);
      onSelect(rows[next].id);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = index < 0 ? 0 : Math.max(index - 1, 0);
      onSelect(rows[prev].id);
    }
  }

  function renderRow(meta: MeetingMeta) {
    const active = meta.id === selectedId;
    return (
      <button
        key={meta.id}
        id={rowId(meta.id)}
        ref={(el) => {
          if (el) rowRefs.current.set(meta.id, el);
          else rowRefs.current.delete(meta.id);
        }}
        role="option"
        aria-selected={active}
        tabIndex={-1}
        className={active ? `${styles.row} ${styles.rowActive}` : styles.row}
        onMouseDown={(e) => {
          // Keep DOM focus on the listbox itself so aria-activedescendant
          // stays correct instead of moving to this (tabIndex=-1) button.
          e.preventDefault();
          listRef.current?.focus();
        }}
        onClick={() => onSelect(meta.id)}
      >
        <div className={styles.rowTop}>
          <span className={styles.title}>{meta.title}</span>
          <span className={styles.time}>{meta.time}</span>
        </div>
        <div className={styles.rowBottom}>
          <span className={styles.sub}>{meta.subtitle}</span>
          {meta.hasSummary && <span className={styles.summaryDot} aria-hidden="true" />}
        </div>
      </button>
    );
  }

  function renderSection(s: Section, depth: number) {
    const open = isOpen(s, openState, expandAll);
    return (
      <div key={s.key} className={styles.section}>
        <button
          type="button"
          className={depth === 0 ? styles.sectionHeader : `${styles.sectionHeader} ${styles.sectionHeaderNested}`}
          aria-expanded={open}
          disabled={expandAll}
          onClick={() => setOpen([s.key], !open)}
        >
          <span className={open ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} aria-hidden="true">›</span>
          <span className={styles.sectionLabel}>{s.label}</span>
          <span className={styles.sectionCount}>{s.count}</span>
        </button>
        {open && (
          <>
            {s.groups.map((g, i) => (
              <Fragment key={g.label ?? `g${i}`}>
                {g.label && (
                  <div className={styles.dayHeader} role="presentation">
                    {g.label}
                  </div>
                )}
                {g.rows.map(renderRow)}
              </Fragment>
            ))}
            {s.children.map((c) => renderSection(c, depth + 1))}
          </>
        )}
      </div>
    );
  }

  return (
    <div
      ref={listRef}
      className={styles.list}
      role="listbox"
      aria-label="Meetings"
      tabIndex={0}
      aria-activedescendant={selectedId && rows.some((r) => r.id === selectedId) ? rowId(selectedId) : undefined}
      onKeyDown={onKeyDown}
    >
      {sections.map((s) => renderSection(s, 0))}
    </div>
  );
}
