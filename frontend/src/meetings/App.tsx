import { useEffect, useMemo, useRef, useState } from 'react';
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
import { LibraryBanner } from './LibraryBanner';
import { EmptyState } from './EmptyState';
import { TodayView } from './TodayView';
import type { TodayConnection } from './TodayView';
import { ConnectClaudeSheet } from './ConnectClaudeSheet';
import { SettingsSheet } from './SettingsSheet';
import { MOCK_METAS, MOCK_DETAILS, MOCK_FILTERS, MOCK_RESULTS, MOCK_STATUS, MOCK_AGENDA, MOCK_UPCOMING } from '../mock/meetings';
import type {
  MeetingMeta,
  MeetingDetail as MeetingDetailType,
  TranscriptLine,
  SearchResult,
  LibraryStatus,
  Filters,
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
  const [popover, setPopover] = useState<PopoverState | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(mockState === 'delete');
  const [today, setToday] = useState(TODAY_STATES.includes(mockState));
  const [connectClaudeOpen, setConnectClaudeOpen] = useState(mockState === 'connect-claude');
  const [settingsOpen, setSettingsOpen] = useState(mockState === 'settings');
  const [embeddedStatus, setEmbeddedStatus] = useState<LibraryStatus>(IDLE_STATUS);
  const todayConnection: TodayConnection =
    mockState === 'today-denied' ? 'denied' : mockState === 'today-unconnected' ? 'unconnected' : 'connected';
  const [jumpTarget, setJumpTarget] = useState<JumpTarget | null>(null);
  const [searchFocusToken, setSearchFocusToken] = useState<number | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const jumpNonceRef = useRef(0);
  const connectClaudeButtonRef = useRef<HTMLButtonElement>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const selectedIdRef = useRef<string | null>(selectedId);
  const filterRef = useRef<SidebarFilter>(filter);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    filterRef.current = filter;
  }, [filter]);

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

  function filterParams(f: SidebarFilter): Record<string, string> {
    if (f.type === 'tag') return { tag: f.value };
    if (f.type === 'person') return { person: f.value };
    return {};
  }

  // Fetches the list for `params`, updates `metas`, then either keeps the
  // current selection (if it's still in the list and `keepSelection`) or
  // selects the first row — fetching that row's detail either way.
  function refreshList(params: Record<string, string>, keepSelection: boolean) {
    return bridge.call<MeetingMeta[]>('meetings.list', params).then((list) => {
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
          .then((d) => setDetails((p) => ({ ...p, [nextId]: d })))
          .catch(() => {});
      }
      return list;
    });
  }

  // Step 1: initial data load + subscriptions, embedded only. Runs once on
  // mount; the meetings.changed handler reads the *current* filter via
  // filterRef so it doesn't need to resubscribe every time the sidebar
  // selection changes (onSelectFilter already re-lists for that case).
  useEffect(() => {
    if (!embedded) return;
    void bridge.call<Filters>('meetings.filters').then(setFilters);
    void refreshList(filterParams(filterRef.current), false);
    void bridge.call<LibraryStatus>('library.status').then(setEmbeddedStatus);

    const offChanged = bridge.on('meetings.changed', () => {
      void bridge.call<Filters>('meetings.filters').then(setFilters);
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

  // Step 3: debounced search already happens in MeetingDetail before
  // onSearchChange fires; here we just call the bridge once it settles.
  useEffect(() => {
    if (!embedded) return;
    if (!searching || searchQuery.trim() === '') {
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    bridge
      .call<SearchResult[]>('meetings.search', { query: searchQuery })
      .then((results) => {
        if (!cancelled) setSearchResults(results);
      })
      .catch(() => {
        if (!cancelled) setSearchResults([]);
      });
    return () => {
      cancelled = true;
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
          if (err instanceof Error && err.message === 'not_found') {
            void refreshList(filterParams(filter), false);
          }
        });
    }
  }

  function onSelectFilter(next: SidebarFilter) {
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
    setToday(true);
    setSearching(false);
    setPopover(null);
    setConfirmOpen(false);
  }

  function onSelectTodayMeeting(meetingId: string) {
    setToday(false);
    select(meetingId);
  }

  function closeConnectClaude() {
    setConnectClaudeOpen(false);
    connectClaudeButtonRef.current?.focus();
  }

  function closeSettings() {
    setSettingsOpen(false);
    settingsButtonRef.current?.focus();
  }

  function onSearchChange(value: string) {
    setSearchQuery(value);
    setSearching(value.trim() !== '');
  }

  function onSelectResult(result: SearchResult) {
    setSearching(false);
    setSearchQuery('');
    select(result.meetingId);
    jumpNonceRef.current += 1;
    setJumpTarget({
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
        })
        .catch(() => {});
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
        .catch(() => {});
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
          select(list[0]?.id ?? null);
        })
        .catch(() => {});
      return;
    }
    const remaining = metas.filter((m) => m.id !== id);
    setMetas(remaining);
    setDetails((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    select(remaining[0]?.id ?? null);
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
          todayCount={isMock && todayConnection === 'connected' ? MOCK_AGENDA.length : undefined}
          onSelectFilter={onSelectFilter}
          onSelectToday={onSelectToday}
          onConnectClaude={() => setConnectClaudeOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
          connectClaudeRef={connectClaudeButtonRef}
          settingsRef={settingsButtonRef}
        />

        {!today &&
          (searching ? (
            <SearchResults
              results={embedded ? searchResults : mockState === 'search' ? MOCK_RESULTS : []}
              onSelect={onSelectResult}
            />
          ) : visibleMetas.length === 0 ? (
            <div className={styles.listColumn}>
              <LibraryBanner status={libraryStatus} />
              <EmptyState title="No meetings yet." body="Record a meeting from the dock to see it here." />
            </div>
          ) : (
            <div className={styles.listColumn}>
              <LibraryBanner status={libraryStatus} />
              <MeetingList
                metas={visibleMetas}
                selectedId={selectedId}
                onSelect={select}
                onRequestSearchFocus={() => setSearchFocusToken((t) => (t ?? 0) + 1)}
                onRequestDelete={() => setConfirmOpen(true)}
              />
            </div>
          ))}

        {today ? (
          <TodayView
            connection={todayConnection}
            agenda={isMock ? MOCK_AGENDA : []}
            upcoming={isMock ? MOCK_UPCOMING : []}
            onSelectMeeting={onSelectTodayMeeting}
            onRecord={(key) => console.log('record', key)}
            onConnectCalendar={() => console.log('connect-calendar')}
            onOpenPrivacySettings={() => console.log('open-privacy-settings')}
          />
        ) : (
          <MeetingDetail
            detail={selectedDetail}
            colorCodeSpeakers={colorCodeSpeakers}
            searchValue={searchQuery}
            onSearchChange={onSearchChange}
            forcedTab={forcedTab}
            autoOpenPopover={mockState === 'popover'}
            onRenameTitle={onRenameTitle}
            onSpeakerClick={onSpeakerClick}
            onRequestDelete={() => setConfirmOpen(true)}
            onCopy={() => {
              if (embedded && selectedId) void bridge.call('meetings.copy', { id: selectedId });
              else console.log('copy', selectedId);
            }}
            onExport={() => {
              if (embedded && selectedId) void bridge.call('meetings.export', { id: selectedId });
              else console.log('export', selectedId);
            }}
            jumpTarget={jumpTarget}
            searchFocusToken={searchFocusToken}
          />
        )}

        {connectClaudeOpen && <ConnectClaudeSheet onClose={closeConnectClaude} />}

        {settingsOpen && (
          <SettingsSheet onClose={closeSettings} onExportAll={() => console.log('export-all-meetings')} />
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
