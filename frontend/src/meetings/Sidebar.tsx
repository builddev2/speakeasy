import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, ReactNode, RefObject } from 'react';
import type { Filters } from '../mock/meetings';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import styles from './Sidebar.module.css';
import { SIDEBAR_KEY_STEP, clampSidebarWidth, parseSidebarWidth } from './sidebarWidth';

const WIDTH_KEY = 'sidebar.width';

export type SidebarFilter = { type: 'all' } | { type: 'tag'; value: string } | { type: 'person'; value: string };

interface SidebarProps {
  filters: Filters;
  activeFilter: SidebarFilter;
  activeToday: boolean;
  activeActions: boolean;
  /** Open items that are mine (everyone's when no name is set) and how many are overdue. */
  actionCounts: { open: number; overdue: number };
  onSelectActions: () => void;
  /** Undefined (no count shown) when Calendar isn't connected. */
  todayCount?: number;
  onSelectFilter: (filter: SidebarFilter) => void;
  onSelectToday: () => void;
  /** Shown above Today while a meeting is being recorded or processed. */
  recordingRow?: { elapsed: string; live: boolean } | null;
  activeRecording: boolean;
  onSelectRecording: () => void;
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

function readWidth(): number | null {
  try {
    return parseSidebarWidth(window.localStorage.getItem(WIDTH_KEY));
  } catch {
    return null;
  }
}

function writeWidth(width: number | null) {
  try {
    if (width === null) window.localStorage.removeItem(WIDTH_KEY);
    else window.localStorage.setItem(WIDTH_KEY, String(width));
  } catch {
    /* storage unavailable: the width just isn't remembered */
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
  activeActions,
  actionCounts,
  onSelectActions,
  todayCount,
  onSelectFilter,
  onSelectToday,
  recordingRow,
  activeRecording,
  onSelectRecording,
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
  const navRef = useRef<HTMLElement>(null);
  // null means the CSS default; only a drag or arrow key pins a width.
  const [width, setWidth] = useState<number | null>(readWidth);
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  function containerWidth(): number {
    return navRef.current?.parentElement?.clientWidth ?? window.innerWidth;
  }

  function currentWidth(): number {
    return navRef.current?.getBoundingClientRect().width ?? width ?? 0;
  }

  // Re-clamp when the window shrinks so the detail pane is never squeezed out.
  useEffect(() => {
    if (width === null) return;
    function onResize() {
      setWidth((w) => (w === null ? w : clampSidebarWidth(w, containerWidth())));
    }
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [width === null]);

  function onHandlePointerDown(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { startX: e.clientX, startWidth: currentWidth() };
  }

  function onHandlePointerMove(e: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    setWidth(clampSidebarWidth(drag.startWidth + e.clientX - drag.startX, containerWidth()));
  }

  function onHandlePointerUp(e: PointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    dragRef.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    setWidth((w) => {
      writeWidth(w);
      return w;
    });
  }

  function onHandleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const delta = e.key === 'ArrowLeft' ? -SIDEBAR_KEY_STEP : e.key === 'ArrowRight' ? SIDEBAR_KEY_STEP : 0;
    if (!delta) return;
    e.preventDefault();
    const next = clampSidebarWidth(currentWidth() + delta, containerWidth());
    setWidth(next);
    writeWidth(next);
  }

  function resetWidth() {
    setWidth(null);
    writeWidth(null);
  }

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
    return !activeToday && !activeActions && !activeRecording && sameFilter(activeFilter, filter) ? `${styles.row} ${styles.rowActive}` : styles.row;
  }

  return (
    <nav
      ref={navRef}
      className={styles.sidebar}
      aria-label="Meetings sidebar"
      style={width === null ? undefined : { width }}
    >
      <div
        className={styles.resizeHandle}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        aria-valuenow={width ?? undefined}
        tabIndex={0}
        title="Drag to resize · double-click to reset"
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={onHandlePointerUp}
        onPointerCancel={onHandlePointerUp}
        onDoubleClick={resetWidth}
        onKeyDown={onHandleKeyDown}
      />
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
        {recordingRow && (
          <button
            className={activeRecording ? `${styles.row} ${styles.rowActive}` : styles.row}
            onClick={onSelectRecording}
          >
            <span className={styles.recLabel}>
              <span className={recordingRow.live ? styles.recDot : `${styles.recDot} ${styles.recDotIdle}`} aria-hidden="true" />
              <span className={styles.rowLabel}>Recording now</span>
            </span>
            <span className={styles.elapsed}>{recordingRow.elapsed}</span>
          </button>
        )}
        {filters.features.calendar && (
          <button
            className={activeToday ? `${styles.row} ${styles.rowActive}` : styles.row}
            onClick={onSelectToday}
          >
            <span className={styles.rowLabel}>Today</span>
            {todayCount !== undefined && <span className={styles.count}>{todayCount}</span>}
          </button>
        )}
        <button className={activeActions ? `${styles.row} ${styles.rowActive}` : styles.row} onClick={onSelectActions}>
          <span className={styles.rowLabel}>Action items</span>
          {actionCounts.overdue > 0 && <span className={styles.overdueBadge} aria-label={`${actionCounts.overdue} overdue`}>{actionCounts.overdue}</span>}
          <span className={styles.count}>{actionCounts.open}</span>
        </button>
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
