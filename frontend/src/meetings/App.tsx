import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { Sidebar } from './Sidebar';
import type { SidebarFilter } from './Sidebar';
import { MeetingList } from './MeetingList';
import { SearchResults } from './SearchResults';
import { MeetingDetail } from './MeetingDetail';
import type { JumpTarget } from './MeetingDetail';
import { SpeakerPopover } from './SpeakerPopover';
import { ConfirmSheet } from './ConfirmSheet';
import { LibraryBanner, useLibraryBannerDismissed } from './LibraryBanner';
import { EmptyState } from './EmptyState';
import { TodayView } from './TodayView';
import type { TodayConnection } from './TodayView';
import { ConnectClaudeSheet } from './ConnectClaudeSheet';
import { SettingsSheet } from './SettingsSheet';
import {
  MOCK_METAS,
  MOCK_DETAILS,
  MOCK_FILTERS,
  MOCK_RESULTS,
  MOCK_STATUS,
  MOCK_AGENDA,
  MOCK_UPCOMING,
  MOCK_CLAUDE_SETUP,
  MOCK_EVENTS_FOR_DAY,
  MOCK_MEETING_SETTINGS,
} from '../mock/meetings';
import type {
  MeetingMeta,
  MeetingDetail as MeetingDetailType,
  TranscriptLine,
  SearchResult,
  LibraryStatus,
  Filters,
  ClaudeSetupInfo,
  AgendaEvent,
  CalendarAccess,
  UpcomingDay,
  EventChip,
  Appearance,
  MeetingSettings,
} from '../mock/meetings';
import { bridge } from '../bridge';
import styles from './App.module.css';

type MockState =
  | 'default'
  | 'empty'
  | 'upgrading'
  | 'upgrade-failed'
  | 'upgrade-skipped'
  | 'search'
  | 'no-summary'
  | 'popover'
  | 'delete'
  | 'today'
  | 'today-denied'
  | 'today-unconnected'
  | 'connect-claude'
  | 'settings';

const KNOWN_STATES: MockState[] = [
  'default',
  'empty',
  'upgrading',
  'upgrade-failed',
  'upgrade-skipped',
  'search',
  'no-summary',
  'popover',
  'delete',
  'today',
  'today-denied',
  'today-unconnected',
  'connect-claude',
  'settings',
];

const TODAY_STATES: MockState[] = ['today', 'today-denied', 'today-unconnected'];

function readMockState(): MockState {
  const raw = new URLSearchParams(window.location.search).get('state');
  return (KNOWN_STATES as string[]).includes(raw ?? '') ? (raw as MockState) : 'default';
}

interface PopoverState {
  line: TranscriptLine;
  anchor: { top: number; left: number };
}

interface MeetingsAppProps {
  colorCodeSpeakers?: boolean;
}

function minutesNow(): number {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}

// A fixed mock "now" (4:29 PM) — it sits between the Design Sync row that's
// still recording (started 3:30) and the 4:30 Roadmap Review Record row, so
// both make sense next to the now-line.
const MOCK_NOW_MINUTES = 16 * 60 + 29;

const IDLE_STATUS: LibraryStatus = { state: 'idle', done: 0, total: 0, skipped: [] };

const EMPTY_FILTERS: Filters = {
  total: 0,
  tags: [],
  people: [],
  features: { calendar: false, claude: false, settings: false },
};

export function MeetingsApp({ colorCodeSpeakers = true }: MeetingsAppProps) {
  // Mock mode is active whenever the page isn't embedded in the native window.
  const isMock = !bridge.embedded;
  const embedded = bridge.embedded;
  const mockState: MockState = isMock ? readMockState() : 'default';

  const [metas, setMetas] = useState<MeetingMeta[]>(isMock && mockState !== 'empty' ? MOCK_METAS : []);
  const [details, setDetails] = useState<Record<string, MeetingDetailType>>(isMock ? MOCK_DETAILS : {});
  const [filters, setFilters] = useState<Filters>(isMock && mockState !== 'empty' ? MOCK_FILTERS : EMPTY_FILTERS);
  const [filter, setFilter] = useState<SidebarFilter>({ type: 'all' });
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    if (!isMock || mockState === 'empty') return null;
    if (mockState === 'no-summary') return 'm-design-review';
    return MOCK_METAS[0]?.id ?? null;
  });
  const [searching, setSearching] = useState(mockState === 'search');
  const [searchQuery, setSearchQuery] = useState(mockState === 'search' ? 'sync' : '');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  // The query whose results have arrived; "No results." waits until it matches the current query.
  const [settledQuery, setSettledQuery] = useState<string | null>(null);
  const [popover, setPopover] = useState<PopoverState | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(mockState === 'delete');
  const [today, setToday] = useState(TODAY_STATES.includes(mockState));
  const [connectClaudeOpen, setConnectClaudeOpen] = useState(mockState === 'connect-claude');
  const [claudeInfo, setClaudeInfo] = useState<ClaudeSetupInfo | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(mockState === 'settings');
  const [embeddedStatus, setEmbeddedStatus] = useState<LibraryStatus>(IDLE_STATUS);
  const mockConnection: TodayConnection =
    mockState === 'today-denied' ? 'denied' : mockState === 'today-unconnected' ? 'unconnected' : 'connected';
  const [calendar, setCalendar] = useState<{ access: CalendarAccess; agenda: AgendaEvent[]; upcoming: UpcomingDay[] }>({
    access: 'unconnected',
    agenda: [],
    upcoming: [],
  });
  const [nowMinutes, setNowMinutes] = useState(() => minutesNow());
  const [meetingSettings, setMeetingSettings] = useState<MeetingSettings | null>(
    isMock ? MOCK_MEETING_SETTINGS : null,
  );
  const todayConnection: TodayConnection = isMock ? mockConnection : calendar.access;
  const [jumpTarget, setJumpTarget] = useState<JumpTarget | null>(null);
  const [searchFocusToken, setSearchFocusToken] = useState<number | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const jumpNonceRef = useRef(0);
  const connectClaudeButtonRef = useRef<HTMLButtonElement>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const selectedIdRef = useRef<string | null>(selectedId);
  const filterRef = useRef<SidebarFilter>(filter);
  // Today is the home view, but only if the user hasn't picked anything before the calendar loads.
  const userNavigatedRef = useRef(false);
  const homeAppliedRef = useRef(false);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    filterRef.current = filter;
  }, [filter]);

  useEffect(() => {
    if (!embedded || homeAppliedRef.current) return;
    if (!filters.features.calendar || calendar.access !== 'connected') return;
    homeAppliedRef.current = true;
    if (!userNavigatedRef.current) setToday(true);
  }, [embedded, filters.features.calendar, calendar.access]);

  // Cmd/Ctrl+K focuses the sidebar search from anywhere in the window.
  const sheetOpen = settingsOpen || confirmOpen || connectClaudeOpen;
  useEffect(() => {
    if (sheetOpen) return;
    function onWindowKeyDown(e: globalThis.KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchFocusToken((t) => (t ?? 0) + 1);
      }
    }
    window.addEventListener('keydown', onWindowKeyDown);
    return () => window.removeEventListener('keydown', onWindowKeyDown);
  }, [sheetOpen]);

  const mockLibraryStatus: LibraryStatus =
    mockState === 'empty'
      ? MOCK_STATUS.idle
      : mockState === 'upgrading'
        ? MOCK_STATUS.upgrading
        : mockState === 'upgrade-failed'
          ? MOCK_STATUS.failed
          : mockState === 'upgrade-skipped'
            ? MOCK_STATUS.doneWithSkips
            : MOCK_STATUS.done;

  const libraryStatus: LibraryStatus = isMock ? mockLibraryStatus : embeddedStatus;
  const bannerDismissed = useLibraryBannerDismissed(libraryStatus);

  function filterParams(f: SidebarFilter): Record<string, string> {
    if (f.type === 'tag') return { tag: f.value };
    if (f.type === 'person') return { person: f.value };
    return {};
  }

  function isNotFoundError(err: unknown): boolean {
    return err instanceof Error && err.message === 'not_found';
  }

  // Shared not_found recovery (Review Focus 2): re-list under the active
  // filter and select the first row. Guarded by selectedIdRef so a stale
  // rejection for a selection the user has since moved away from doesn't
  // yank them back (fix round 1, item 6).
  function recoverFromNotFound(id: string) {
    if (selectedIdRef.current !== id) return;
    void refreshList(filterParams(filterRef.current), false);
  }

  // Bumped on every refreshList call so an in-flight response that's no
  // longer the latest request can't clobber a newer one (fix round 1, item 5).
  const listRequestIdRef = useRef(0);

  // Fetches the list for `params`, updates `metas`, then either keeps the
  // current selection (if it's still in the list and `keepSelection`) or
  // selects the first row — fetching that row's detail either way.
  function refreshList(params: Record<string, string>, keepSelection: boolean) {
    const requestId = ++listRequestIdRef.current;
    return bridge
      .call<MeetingMeta[]>('meetings.list', params)
      .then((list) => {
        if (listRequestIdRef.current !== requestId) return list; // superseded by a newer request
        setMetas(list);
        const prev = selectedIdRef.current;
        const keep = keepSelection && !!prev && list.some((m) => m.id === prev);
        const nextId = keep ? prev : (list[0]?.id ?? null);
        if (nextId !== prev) {
          setSelectedId(nextId);
          setPopover(null);
          setJumpTarget(null);
        }
        if (nextId) {
          bridge
            .call<MeetingDetailType>('meetings.get', { id: nextId })
            .then((d) => {
              if (listRequestIdRef.current !== requestId) return; // superseded
              setDetails((p) => ({ ...p, [nextId]: d }));
            })
            .catch((err) => console.error('meetings.get failed', err));
        }
        return list;
      })
      .catch((err) => {
        console.error('meetings.list failed', err);
        return [] as MeetingMeta[];
      });
  }

  // Step 1: initial data load + subscriptions, embedded only. Runs once on
  // mount; the meetings.changed handler reads the *current* filter via
  // filterRef so it doesn't need to resubscribe every time the sidebar
  // selection changes (onSelectFilter already re-lists for that case).
  useEffect(() => {
    if (!embedded) return;
    void bridge
      .call<Filters>('meetings.filters')
      .then(setFilters)
      .catch((err) => console.error('meetings.filters failed', err));
    void refreshList(filterParams(filterRef.current), false);
    void bridge
      .call<LibraryStatus>('library.status')
      .then(setEmbeddedStatus)
      .catch((err) => console.error('library.status failed', err));

    const offChanged = bridge.on('meetings.changed', () => {
      void bridge
        .call<Filters>('meetings.filters')
        .then(setFilters)
        .catch((err) => console.error('meetings.filters failed', err));
      void refreshList(filterParams(filterRef.current), true);
    });
    const offProgress = bridge.on('library.progress', (payload) => {
      setEmbeddedStatus(payload as LibraryStatus);
    });
    return () => {
      offChanged();
      offProgress();
    };
    // Mount-only: deliberately excludes `filter` (read via filterRef instead).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded]);

  // Today's agenda and Upcoming come from the calendar thread's cached table.
  const refreshCalendar = useCallback(() => {
    if (!embedded) return;
    void Promise.all([
      bridge.call<{ access: CalendarAccess; agenda: AgendaEvent[] }>('calendar.today'),
      bridge.call<{ days: UpcomingDay[] }>('calendar.upcoming'),
    ])
      .then(([todayData, upcomingData]) =>
        setCalendar({ access: todayData.access, agenda: todayData.agenda, upcoming: upcomingData.days }),
      )
      .catch((err) => console.error('calendar refresh failed', err));
  }, [embedded]);

  useEffect(() => {
    refreshCalendar();
    // A new recording flips an agenda row to Recorded, so meetings.changed refreshes too.
    const offCalendar = bridge.on('calendar.changed', refreshCalendar);
    const offMeetings = bridge.on('meetings.changed', refreshCalendar);
    return () => {
      offCalendar();
      offMeetings();
    };
  }, [refreshCalendar]);

  useEffect(() => {
    // The now-line and Record buttons move with time.
    const id = window.setInterval(() => {
      setNowMinutes(minutesNow());
      refreshCalendar();
    }, 30_000);
    return () => window.clearInterval(id);
  }, [refreshCalendar]);

  // Settings are fetched on each open so they reflect the latest calendar list.
  useEffect(() => {
    if (!embedded) return;
    if (!settingsOpen) {
      setMeetingSettings(null);
      return;
    }
    bridge
      .call<MeetingSettings>('settings.meetings.get')
      .then(setMeetingSettings)
      .catch((err) => {
        console.error('settings.meetings.get failed', err);
        setSettingsOpen(false);
      });
  }, [embedded, settingsOpen]);

  function onChangeSettings(patch: { offerToRecord?: boolean; detectCalls?: boolean; appearance?: Appearance; calendars?: Record<string, boolean> }) {
    if (embedded) {
      bridge
        .call<MeetingSettings>('settings.meetings.set', patch)
        .then(setMeetingSettings)
        .catch((err) => console.error('settings.meetings.set failed', err));
      return;
    }
    setMeetingSettings((prev) =>
      prev
        ? {
            offerToRecord: patch.offerToRecord ?? prev.offerToRecord,
            detectCalls: patch.detectCalls ?? prev.detectCalls,
            appearance: patch.appearance ?? prev.appearance,
            accounts: prev.accounts.map((account) => ({
              ...account,
              calendars: account.calendars.map((cal) =>
                patch.calendars && cal.id in patch.calendars ? { ...cal, enabled: patch.calendars[cal.id] } : cal,
              ),
            })),
          }
        : prev,
    );
  }

  function loadEventsForMeeting(): Promise<EventChip[]> {
    if (!embedded) return Promise.resolve(MOCK_EVENTS_FOR_DAY);
    if (!selectedId) return Promise.resolve([]);
    return bridge.call<EventChip[]>('meetings.eventsForDay', { id: selectedId });
  }

  function onLinkEvent(key: string | null) {
    if (!selectedId) return;
    const id = selectedId;
    if (embedded) {
      bridge
        .call<MeetingDetailType>('meetings.linkEvent', { id, key })
        .then((d) => {
          setDetails((prev) => ({ ...prev, [id]: d }));
          refreshCalendar();
          // Linking overwrites the title and people, so the list row and the
          // sidebar counts go stale unless we re-list (selection is kept).
          void refreshList(filterParams(filterRef.current), true);
          void bridge
            .call<Filters>('meetings.filters')
            .then(setFilters)
            .catch((err) => console.error('meetings.filters failed', err));
        })
        .catch((err) => {
          if (isNotFoundError(err)) recoverFromNotFound(id);
          else console.error('meetings.linkEvent failed', err);
        });
      return;
    }
    const chosen = key === null ? null : (MOCK_EVENTS_FOR_DAY.find((e) => e.key === key) ?? null);
    setDetails((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], event: chosen } } : prev));
    if (chosen) {
      setDetails((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], title: chosen.title } } : prev));
      setMetas((prev) => prev.map((m) => (m.id === id ? { ...m, title: chosen.title } : m)));
    }
  }

  // Step 3: debounced search already happens in MeetingDetail before
  // onSearchChange fires; here we just call the bridge once it settles.
  useEffect(() => {
    if (!embedded) return;
    if (!searching || searchQuery.trim() === '') {
      setSearchResults([]);
      setSettledQuery(null);
      return;
    }
    let cancelled = false;
    // The sidebar field updates on every keystroke; settle for 250 ms before asking the bridge.
    const timer = window.setTimeout(() => {
      bridge
        .call<SearchResult[]>('meetings.search', { query: searchQuery })
        .then((results) => {
          if (cancelled) return;
          setSearchResults(results);
          setSettledQuery(searchQuery);
        })
        .catch(() => {
          if (cancelled) return;
          setSearchResults([]);
          setSettledQuery(searchQuery);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [embedded, searching, searchQuery]);

  const visibleMetas = useMemo(() => {
    if (filter.type === 'all') return metas;
    if (filter.type === 'tag') return metas.filter((m) => m.tags.includes(filter.value));
    return metas.filter((m) => m.people.includes(filter.value));
  }, [metas, filter]);

  // Step 2: selecting a row calls meetings.get. A not_found rejection
  // (row deleted/renamed elsewhere) re-lists and selects the first row.
  function select(id: string | null) {
    setSelectedId(id);
    setPopover(null);
    setJumpTarget(null);
    if (embedded && id) {
      bridge
        .call<MeetingDetailType>('meetings.get', { id })
        .then((d) => setDetails((prev) => ({ ...prev, [id]: d })))
        .catch((err) => {
          if (isNotFoundError(err)) recoverFromNotFound(id);
          else console.error('meetings.get failed', err);
        });
    }
  }

  function onSelectFilter(next: SidebarFilter) {
    userNavigatedRef.current = true;
    setFilter(next);
    setSearching(false);
    setToday(false);
    if (embedded) {
      void refreshList(filterParams(next), false);
      return;
    }
    const list =
      next.type === 'all'
        ? metas
        : metas.filter((m) => (next.type === 'tag' ? m.tags.includes(next.value) : m.people.includes(next.value)));
    select(list[0]?.id ?? null);
  }

  function onSelectToday() {
    userNavigatedRef.current = true;
    setToday(true);
    setSearching(false);
    setPopover(null);
    setConfirmOpen(false);
  }

  function onSelectTodayMeeting(meetingId: string) {
    setToday(false);
    select(meetingId);
  }

  // Fetched on each open so "last used" is fresh; cleared on close so a stale value never flashes.
  useEffect(() => {
    if (!connectClaudeOpen) {
      setClaudeInfo(null);
      return;
    }
    if (embedded) {
      bridge
        .call<ClaudeSetupInfo>('claude.setupInfo')
        .then(setClaudeInfo)
        .catch((err) => console.error('claude.setupInfo failed', err));
    } else {
      setClaudeInfo(MOCK_CLAUDE_SETUP);
    }
  }, [connectClaudeOpen, embedded]);

  function copyForClaude(text: string): Promise<void> {
    // WKWebView can reject navigator.clipboard; the native bridge cannot.
    if (embedded) return bridge.call('meetings.copyText', { text }).then(() => undefined);
    return navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.resolve();
  }
  const installClaudeExtension = () =>
    embedded ? bridge.call('claude.installExtension').then(() => undefined) : Promise.resolve();
  const revealClaudeConfig = () =>
    embedded ? bridge.call('claude.revealConfig').then(() => undefined) : Promise.resolve();

  function closeConnectClaude() {
    setConnectClaudeOpen(false);
    connectClaudeButtonRef.current?.focus();
  }

  function closeSettings() {
    setSettingsOpen(false);
    settingsButtonRef.current?.focus();
  }

  function onSearchChange(value: string) {
    userNavigatedRef.current = true;
    setSearchQuery(value);
    setSearching(value.trim() !== '');
  }

  function onSelectResult(result: SearchResult) {
    userNavigatedRef.current = true;
    setToday(false);
    setSearching(false);
    setSearchQuery('');
    select(result.meetingId);
    jumpNonceRef.current += 1;
    setJumpTarget({
      meetingId: result.meetingId,
      segmentIndex: result.segmentIndex,
      seconds: result.seconds,
      kind: result.kind,
      nonce: jumpNonceRef.current,
    });
  }

  function onRenameTitle(title: string) {
    if (!selectedId) return;
    const id = selectedId;
    if (embedded) {
      bridge
        .call<MeetingMeta[]>('meetings.rename', { id, title, ...filterParams(filter) })
        .then((list) => {
          setMetas(list);
          setDetails((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], title } } : prev));
          // Renaming doesn't fire `meetings.changed` (that's only emitted for
          // library imports), so the sidebar's tag/people counts and total
          // would otherwise go stale until the next unrelated refresh.
          void bridge
            .call<Filters>('meetings.filters')
            .then(setFilters)
            .catch((err) => console.error('meetings.filters failed', err));
        })
        .catch((err) => {
          if (isNotFoundError(err)) recoverFromNotFound(id);
          else console.error('meetings.rename failed', err);
        });
      return;
    }
    setDetails((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], title } } : prev));
    setMetas((prev) => prev.map((m) => (m.id === id ? { ...m, title } : m)));
  }

  function onSpeakerClick(line: TranscriptLine, absoluteAnchor: { top: number; left: number }) {
    const containerRect = containerRef.current?.getBoundingClientRect();
    const anchor = containerRect
      ? { top: absoluteAnchor.top - containerRect.top + 6, left: absoluteAnchor.left - containerRect.left }
      : absoluteAnchor;
    setPopover({ line, anchor });
  }

  function onRenameSpeaker(name: string, allMatching: boolean) {
    if (!selectedId || !popover) return;
    const id = selectedId;
    const target = popover.line;
    if (embedded) {
      bridge
        .call<MeetingDetailType>('meetings.relabelSpeaker', {
          id,
          segmentIndex: target.segmentIndex,
          label: name,
          allMatching,
        })
        .then((detail) => setDetails((prev) => ({ ...prev, [id]: detail })))
        .catch((err) => {
          if (isNotFoundError(err)) recoverFromNotFound(id);
          else console.error('meetings.relabelSpeaker failed', err);
        });
      setPopover(null);
      return;
    }
    setDetails((prev) => {
      const current = prev[id];
      if (!current) return prev;
      const lines = current.lines.map((l) => {
        const matches = allMatching ? l.speakerNumber === target.speakerNumber : l.segmentIndex === target.segmentIndex;
        return matches ? { ...l, speakerLabel: name } : l;
      });
      return { ...prev, [id]: { ...current, lines } };
    });
    setPopover(null);
  }

  // Selects the row after the deleted one in the (pre-delete) visible
  // order, or the previous row if the deleted one was last — the brief's
  // "select the next row", not always the first (fix round 1, item 8).
  function nextSelectionAfterDelete(id: string, remainingVisible: MeetingMeta[]): string | null {
    const visibleIndex = visibleMetas.findIndex((m) => m.id === id);
    // Deleted id wasn't in visibleMetas (shouldn't normally happen — fall
    // back to the first row rather than clamping -1 into "select nothing".
    const baseIndex = visibleIndex === -1 ? 0 : visibleIndex;
    const nextIndex = Math.min(baseIndex, remainingVisible.length - 1);
    return nextIndex >= 0 ? remainingVisible[nextIndex].id : null;
  }

  function onConfirmDelete() {
    if (!selectedId) {
      setConfirmOpen(false);
      return;
    }
    const id = selectedId;
    setConfirmOpen(false);
    if (embedded) {
      bridge
        .call<MeetingMeta[]>('meetings.delete', { id, ...filterParams(filter) })
        .then((list) => {
          setMetas(list);
          setDetails((prev) => {
            const next = { ...prev };
            delete next[id];
            return next;
          });
          // `list` already reflects the active tag/person filter, so it
          // doubles as the "remaining visible" list.
          select(nextSelectionAfterDelete(id, list));
          // Deleting doesn't fire `meetings.changed` either (see the rename
          // handler above) — refetch so the sidebar total and tag/people
          // counts stay correct.
          void bridge
            .call<Filters>('meetings.filters')
            .then(setFilters)
            .catch((err) => console.error('meetings.filters failed', err));
        })
        .catch((err) => {
          if (isNotFoundError(err)) recoverFromNotFound(id);
          else console.error('meetings.delete failed', err);
        });
      return;
    }
    const remaining = metas.filter((m) => m.id !== id);
    setMetas(remaining);
    setDetails((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    select(nextSelectionAfterDelete(id, visibleMetas.filter((m) => m.id !== id)));
  }

  const selectedDetail = selectedId ? details[selectedId] ?? null : null;
  const forcedTab = mockState === 'no-summary' ? 'summary' : mockState === 'popover' ? 'transcript' : undefined;

  return (
    <GlassPanel width={1040} height={660}>
      <TitleBar title="Meetings" />
      <div className={styles.split} ref={containerRef}>
        <Sidebar
          filters={filters}
          activeFilter={filter}
          activeToday={today}
          todayCount={
            todayConnection === 'connected' ? (isMock ? MOCK_AGENDA : calendar.agenda).length : undefined
          }
          onSelectFilter={onSelectFilter}
          onSelectToday={onSelectToday}
          searchValue={searchQuery}
          onSearchChange={onSearchChange}
          searchFocusToken={searchFocusToken}
          onConnectClaude={() => setConnectClaudeOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
          connectClaudeRef={connectClaudeButtonRef}
          settingsRef={settingsButtonRef}
        >
          <LibraryBanner status={libraryStatus} dismissed={bannerDismissed} />
          {searching ? (
            <SearchResults
              results={embedded ? searchResults : mockState === 'search' ? MOCK_RESULTS : []}
              pending={embedded && settledQuery !== searchQuery}
              onSelect={onSelectResult}
            />
          ) : visibleMetas.length === 0 ? (
            libraryStatus.state !== 'upgrading' ? (
              <EmptyState title="No meetings yet." body="Record a meeting from the dock to see it here." />
            ) : (
              <div />
            )
          ) : (
            <MeetingList
              metas={visibleMetas}
              selectedId={today ? null : selectedId}
              onSelect={(id) => {
                userNavigatedRef.current = true;
                setToday(false);
                select(id);
              }}
              onRequestSearchFocus={() => setSearchFocusToken((t) => (t ?? 0) + 1)}
              onRequestDelete={() => setConfirmOpen(true)}
            />
          )}
        </Sidebar>

        {today ? (
          <TodayView
            connection={todayConnection}
            agenda={isMock ? MOCK_AGENDA : calendar.agenda}
            upcoming={isMock ? MOCK_UPCOMING : calendar.upcoming}
            nowMinutes={isMock ? MOCK_NOW_MINUTES : nowMinutes}
            onSelectMeeting={onSelectTodayMeeting}
            onRecord={(key) => {
              if (embedded) {
                void bridge.call('calendar.record', { key }).catch((err) => console.error('calendar.record failed', err));
              } else console.log('record', key);
            }}
            onConnectCalendar={() => {
              if (embedded) {
                void bridge
                  .call('calendar.requestAccess')
                  .then(refreshCalendar)
                  .catch((err) => console.error('calendar.requestAccess failed', err));
              } else console.log('connect-calendar');
            }}
            onOpenPrivacySettings={() => {
              if (embedded) {
                void bridge
                  .call('calendar.openPrivacySettings')
                  .catch((err) => console.error('calendar.openPrivacySettings failed', err));
              } else console.log('open-privacy-settings');
            }}
          />
        ) : (
          <MeetingDetail
            detail={selectedDetail}
            colorCodeSpeakers={colorCodeSpeakers}
            forcedTab={forcedTab}
            autoOpenPopover={mockState === 'popover'}
            onRenameTitle={onRenameTitle}
            onSpeakerClick={onSpeakerClick}
            onRequestDelete={() => setConfirmOpen(true)}
            onCopy={() => {
              if (embedded && selectedId) {
                const id = selectedId;
                void bridge.call('meetings.copy', { id }).catch((err) => {
                  if (isNotFoundError(err)) recoverFromNotFound(id);
                  else console.error('meetings.copy failed', err);
                });
              } else {
                console.log('copy', selectedId);
              }
            }}
            onExport={() => {
              if (embedded && selectedId) {
                const id = selectedId;
                void bridge.call('meetings.export', { id }).catch((err) => {
                  if (isNotFoundError(err)) recoverFromNotFound(id);
                  else console.error('meetings.export failed', err);
                });
              } else {
                console.log('export', selectedId);
              }
            }}
            onRequestSummary={() => {
              if (embedded && selectedId) {
                const id = selectedId;
                void bridge
                  .call<MeetingDetailType>('meetings.requestSummary', { id })
                  .then((d) => setDetails((prev) => ({ ...prev, [id]: d })))
                  .catch((err) => {
                    if (isNotFoundError(err)) recoverFromNotFound(id);
                    else console.error('meetings.requestSummary failed', err);
                  });
              } else {
                console.log('request-summary', selectedId);
              }
            }}
            jumpTarget={jumpTarget}
            onLoadEvents={isMock || filters.features.calendar ? loadEventsForMeeting : undefined}
            onLinkEvent={isMock || filters.features.calendar ? onLinkEvent : undefined}
          />
        )}

        {connectClaudeOpen && (
          <ConnectClaudeSheet
            onClose={closeConnectClaude}
            info={claudeInfo}
            onCopy={copyForClaude}
            onInstall={installClaudeExtension}
            onRevealConfig={revealClaudeConfig}
          />
        )}

        {settingsOpen && meetingSettings && (
          <SettingsSheet
            settings={meetingSettings}
            calendarConnected={todayConnection === 'connected'}
            onChange={onChangeSettings}
            onClose={closeSettings}
            onExportAll={() => console.log('export-all-meetings')}
          />
        )}

        {popover && (
          <SpeakerPopover
            label={popover.line.speakerLabel}
            anchor={popover.anchor}
            onCancel={() => setPopover(null)}
            onRename={onRenameSpeaker}
          />
        )}

        {confirmOpen && selectedDetail && (
          <ConfirmSheet
            title={`Delete ‘${selectedDetail.title}’?`}
            body="The transcript and notes will be removed. This can't be undone."
            confirmLabel="Delete"
            onCancel={() => setConfirmOpen(false)}
            onConfirm={onConfirmDelete}
          />
        )}
      </div>
    </GlassPanel>
  );
}
