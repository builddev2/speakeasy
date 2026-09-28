import type { Filters } from '../mock/meetings';
import styles from './Sidebar.module.css';

export type SidebarFilter = { type: 'all' } | { type: 'tag'; value: string } | { type: 'person'; value: string };

interface SidebarProps {
  filters: Filters;
  activeFilter: SidebarFilter;
  activeToday: boolean;
  onSelectFilter: (filter: SidebarFilter) => void;
  onSelectToday: () => void;
  onConnectClaude: () => void;
  onOpenSettings: () => void;
}

function sameFilter(a: SidebarFilter, b: SidebarFilter): boolean {
  if (a.type !== b.type) return false;
  if (a.type === 'all') return true;
  return (a as { value: string }).value === (b as { value: string }).value;
}

export function Sidebar({
  filters,
  activeFilter,
  activeToday,
  onSelectFilter,
  onSelectToday,
  onConnectClaude,
  onOpenSettings,
}: SidebarProps) {
  return (
    <nav className={styles.sidebar} aria-label="Meetings sidebar">
      {filters.features.calendar && (
        <div className={styles.section}>
          <div className={styles.heading}>Today</div>
          <button
            className={activeToday ? `${styles.row} ${styles.rowActive}` : styles.row}
            onClick={onSelectToday}
          >
            <span className={styles.rowLabel}>Today</span>
          </button>
        </div>
      )}

      <div className={styles.section}>
        <div className={styles.heading}>Library</div>
        <button
          className={
            !activeToday && sameFilter(activeFilter, { type: 'all' }) ? `${styles.row} ${styles.rowActive}` : styles.row
          }
          onClick={() => onSelectFilter({ type: 'all' })}
        >
          <span className={styles.rowLabel}>All Meetings</span>
          <span className={styles.count}>{filters.total}</span>
        </button>
      </div>

      {filters.tags.length > 0 && (
        <div className={styles.section}>
          <div className={styles.heading}>Tags</div>
          {filters.tags.map((tag) => (
            <button
              key={tag.name}
              className={
                !activeToday && sameFilter(activeFilter, { type: 'tag', value: tag.name })
                  ? `${styles.row} ${styles.rowActive}`
                  : styles.row
              }
              onClick={() => onSelectFilter({ type: 'tag', value: tag.name })}
            >
              <span className={styles.rowLabel}>{tag.name}</span>
              <span className={styles.count}>{tag.count}</span>
            </button>
          ))}
        </div>
      )}

      {filters.people.length > 0 && (
        <div className={styles.section}>
          <div className={styles.heading}>People</div>
          {filters.people.map((person) => (
            <button
              key={person.name}
              className={
                !activeToday && sameFilter(activeFilter, { type: 'person', value: person.name })
                  ? `${styles.row} ${styles.rowActive}`
                  : styles.row
              }
              onClick={() => onSelectFilter({ type: 'person', value: person.name })}
            >
              <span className={styles.rowLabel}>{person.name}</span>
              <span className={styles.count}>{person.count}</span>
            </button>
          ))}
        </div>
      )}

      <div className={styles.spacer} />

      {(filters.features.claude || filters.features.settings) && (
        <div className={styles.footer}>
          {filters.features.claude && (
            <button className={styles.footerRow} onClick={onConnectClaude}>
              Connect Claude
            </button>
          )}
          {filters.features.settings && (
            <button className={styles.gear} aria-label="Settings" title="Settings" onClick={onOpenSettings}>
              ⚙
            </button>
          )}
        </div>
      )}
    </nav>
  );
}
