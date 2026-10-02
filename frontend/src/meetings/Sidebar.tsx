import { useEffect, useRef, useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import type { Filters } from '../mock/meetings';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import styles from './Sidebar.module.css';

export type SidebarFilter = { type: 'all' } | { type: 'tag'; value: string } | { type: 'person'; value: string };

interface SidebarProps {
  filters: Filters;
  activeFilter: SidebarFilter;
  activeToday: boolean;
  /** Undefined (no count shown) when Calendar isn't connected. */
  todayCount?: number;
  onSelectFilter: (filter: SidebarFilter) => void;
  onSelectToday: () => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchFocusToken?: number;
  /** The meeting list or search results, rendered under the nav. */
  children: ReactNode;
  onConnectClaude: () => void;
  onOpenSettings: () => void;
  connectClaudeRef?: RefObject<HTMLButtonElement>;
  settingsRef?: RefObject<HTMLButtonElement>;
}

function sameFilter(a: SidebarFilter, b: SidebarFilter): boolean {
  if (a.type !== b.type) return false;
  if (a.type === 'all') return true;
  return (a as { value: string }).value === (b as { value: string }).value;
}

function readOpen(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

function writeOpen(key: string, open: boolean) {
  try {
    window.localStorage.setItem(key, open ? '1' : '0');
  } catch {
    /* storage unavailable: the state just isn't remembered */
  }
}

export function Sidebar({
  filters,
  activeFilter,
  activeToday,
  todayCount,
  onSelectFilter,
  onSelectToday,
  searchValue,
  onSearchChange,
  searchFocusToken,
  children,
  onConnectClaude,
  onOpenSettings,
  connectClaudeRef,
  settingsRef,
}: SidebarProps) {
  const [tagsOpen, setTagsOpen] = useState(() => readOpen('sidebar.tags.open'));
  const [peopleOpen, setPeopleOpen] = useState(() => readOpen('sidebar.people.open'));
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (searchFocusToken === undefined) return;
    searchRef.current?.focus();
    searchRef.current?.select();
  }, [searchFocusToken]);

  function toggleTags() {
    writeOpen('sidebar.tags.open', !tagsOpen);
    setTagsOpen(!tagsOpen);
  }

  function togglePeople() {
    writeOpen('sidebar.people.open', !peopleOpen);
    setPeopleOpen(!peopleOpen);
  }

  function rowClass(filter: SidebarFilter): string {
    return !activeToday && sameFilter(activeFilter, filter) ? `${styles.row} ${styles.rowActive}` : styles.row;
  }

  return (
    <nav className={styles.sidebar} aria-label="Meetings sidebar">
      <div className={styles.searchWrap}>
        <input
          ref={searchRef}
          {...NO_AUTOCORRECT}
          className={styles.searchInput}
          placeholder="Search"
          aria-label="Search meetings"
          value={searchValue}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault();
              onSearchChange('');
            }
          }}
        />
        <span className={styles.hint} aria-hidden="true">
          ⌘K
        </span>
      </div>

      <div className={styles.nav}>
        {filters.features.calendar && (
          <button
            className={activeToday ? `${styles.row} ${styles.rowActive}` : styles.row}
            onClick={onSelectToday}
          >
            <span className={styles.rowLabel}>Today</span>
            {todayCount !== undefined && <span className={styles.count}>{todayCount}</span>}
          </button>
        )}
        <button className={rowClass({ type: 'all' })} onClick={() => onSelectFilter({ type: 'all' })}>
          <span className={styles.rowLabel}>All meetings</span>
          <span className={styles.count}>{filters.total}</span>
        </button>

        {filters.tags.length > 0 && (
          <>
            <button className={styles.disclosure} aria-expanded={tagsOpen} onClick={toggleTags}>
              <span className={tagsOpen ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} aria-hidden="true">
                ›
              </span>
              <span className={styles.rowLabel}>Tags</span>
            </button>
            {tagsOpen &&
              filters.tags.map((tag) => (
                <button
                  key={tag.name}
                  className={`${rowClass({ type: 'tag', value: tag.name })} ${styles.indented}`}
                  onClick={() => onSelectFilter({ type: 'tag', value: tag.name })}
                >
                  <span className={styles.rowLabel}>{tag.name}</span>
                  <span className={styles.count}>{tag.count}</span>
                </button>
              ))}
          </>
        )}

        {filters.people.length > 0 && (
          <>
            <button className={styles.disclosure} aria-expanded={peopleOpen} onClick={togglePeople}>
              <span
                className={peopleOpen ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron}
                aria-hidden="true"
              >
                ›
              </span>
              <span className={styles.rowLabel}>People</span>
            </button>
            {peopleOpen &&
              filters.people.map((person) => (
                <button
                  key={person.name}
                  className={`${rowClass({ type: 'person', value: person.name })} ${styles.indented}`}
                  onClick={() => onSelectFilter({ type: 'person', value: person.name })}
                >
                  <span className={styles.rowLabel}>{person.name}</span>
                  <span className={styles.count}>{person.count}</span>
                </button>
              ))}
          </>
        )}
      </div>

      <div className={styles.content}>{children}</div>

      {(filters.features.claude || filters.features.settings) && (
        <div className={styles.footer}>
          {filters.features.claude && (
            <button ref={connectClaudeRef} className={styles.footerRow} onClick={onConnectClaude}>
              Connect Claude
            </button>
          )}
          {filters.features.settings && (
            <button
              ref={settingsRef}
              className={styles.gear}
              aria-label="Settings"
              title="Settings"
              onClick={onOpenSettings}
            >
              ⚙
            </button>
          )}
        </div>
      )}
    </nav>
  );
}
