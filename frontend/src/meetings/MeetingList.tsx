import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';
import type { MeetingMeta } from '../mock/meetings';
import styles from './MeetingList.module.css';

interface MeetingListProps {
  metas: MeetingMeta[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRequestSearchFocus?: () => void;
  onRequestDelete?: () => void;
}

interface Group {
  dayLabel: string;
  rows: MeetingMeta[];
}

function rowId(id: string): string {
  return `meeting-row-${id}`;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;
}

function groupByDay(metas: MeetingMeta[]): Group[] {
  const groups: Group[] = [];
  for (const meta of metas) {
    const last = groups[groups.length - 1];
    if (last && last.dayLabel === meta.dayLabel) {
      last.rows.push(meta);
    } else {
      groups.push({ dayLabel: meta.dayLabel, rows: [meta] });
    }
  }
  return groups;
}

export function MeetingList({ metas, selectedId, onSelect, onRequestSearchFocus, onRequestDelete }: MeetingListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const groups = groupByDay(metas);

  useEffect(() => {
    if (!selectedId) return;
    rowRefs.current.get(selectedId)?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

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
    if (metas.length === 0) return;
    const index = metas.findIndex((m) => m.id === selectedId);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = index < 0 ? 0 : Math.min(index + 1, metas.length - 1);
      onSelect(metas[next].id);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = index < 0 ? 0 : Math.max(index - 1, 0);
      onSelect(metas[prev].id);
    }
  }

  return (
    <div
      ref={listRef}
      className={styles.list}
      role="listbox"
      aria-label="Meetings"
      tabIndex={0}
      aria-activedescendant={selectedId ? rowId(selectedId) : undefined}
      onKeyDown={onKeyDown}
    >
      {groups.map((group) => (
        <div key={group.dayLabel}>
          <div className={styles.dayHeader} role="presentation">
            {group.dayLabel}
          </div>
          {group.rows.map((meta) => {
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
                onClick={() => onSelect(meta.id)}
              >
                <div className={styles.rowTop}>
                  <span className={styles.rowTitleLine}>
                    <span className={styles.time}>{meta.time}</span>
                    <span className={styles.dot}>·</span>
                    <span className={styles.title}>{meta.title}</span>
                  </span>
                  <span className={styles.rowMeta}>
                    <span className={styles.duration}>{meta.duration}</span>
                    {meta.hasSummary && <span className={styles.summaryDot} aria-hidden="true" />}
                  </span>
                </div>
                <div className={styles.sub}>{meta.subtitle}</div>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}
