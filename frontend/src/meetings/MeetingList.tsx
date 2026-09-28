import { useRef } from 'react';
import type { KeyboardEvent } from 'react';
import type { MeetingMeta } from '../mock/meetings';
import styles from './MeetingList.module.css';

interface MeetingListProps {
  metas: MeetingMeta[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRequestSearchFocus?: () => void;
}

interface Group {
  dayLabel: string;
  rows: MeetingMeta[];
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

export function MeetingList({ metas, selectedId, onSelect, onRequestSearchFocus }: MeetingListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const groups = groupByDay(metas);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
      e.preventDefault();
      onRequestSearchFocus?.();
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
      onKeyDown={onKeyDown}
    >
      {groups.map((group) => (
        <div key={group.dayLabel}>
          <div className={styles.dayHeader}>{group.dayLabel}</div>
          {group.rows.map((meta) => {
            const active = meta.id === selectedId;
            return (
              <button
                key={meta.id}
                role="option"
                aria-selected={active}
                className={active ? `${styles.row} ${styles.rowActive}` : styles.row}
                onClick={() => onSelect(meta.id)}
              >
                <div className={styles.rowTop}>
                  <span className={styles.rowTitleLine}>
                    <span className={styles.time}>{meta.time}</span>
                    <span className={styles.dot}>·</span>
                    <span className={styles.title}>{meta.title}</span>
                  </span>
                  {meta.hasSummary && <span className={styles.summaryDot} aria-hidden="true" />}
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
