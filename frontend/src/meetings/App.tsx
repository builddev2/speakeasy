import { useEffect, useMemo, useRef, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { Sidebar } from './Sidebar';
import type { SidebarFilter } from './Sidebar';
import { MeetingList } from './MeetingList';
import { SearchResults } from './SearchResults';
import { MeetingDetail } from './MeetingDetail';
import { SpeakerPopover } from './SpeakerPopover';
import { ConfirmSheet } from './ConfirmSheet';
import { LibraryBanner } from './LibraryBanner';
import { EmptyState } from './EmptyState';
import { MOCK_METAS, MOCK_DETAILS, MOCK_FILTERS, MOCK_RESULTS, MOCK_STATUS } from '../mock/meetings';
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
  | 'search'
  | 'no-summary'
  | 'popover'
  | 'delete';

const KNOWN_STATES: MockState[] = [
  'default',
  'empty',
  'upgrading',
  'upgrade-failed',
  'search',
  'no-summary',
  'popover',
  'delete',
];

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

export function MeetingsApp({ colorCodeSpeakers = true }: MeetingsAppProps) {
  // Mock mode is active whenever the page isn't embedded in the native window.
  // Task 10 wires the embedded path to the bridge; until then it starts empty.
  const isMock = !bridge.embedded;
  const mockState: MockState = isMock ? readMockState() : 'default';

  const [metas, setMetas] = useState<MeetingMeta[]>(isMock && mockState !== 'empty' ? MOCK_METAS : []);
  const [details, setDetails] = useState<Record<string, MeetingDetailType>>(isMock ? MOCK_DETAILS : {});
  const [filters] = useState<Filters>(
    isMock && mockState !== 'empty' ? MOCK_FILTERS : { total: 0, tags: [], people: [], features: { calendar: false, claude: false, settings: false } },
  );
  const [filter, setFilter] = useState<SidebarFilter>({ type: 'all' });
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    if (!isMock || mockState === 'empty') return null;
    if (mockState === 'no-summary') return 'm-design-review';
    return MOCK_METAS[0]?.id ?? null;
  });
  const [searching, setSearching] = useState(mockState === 'search');
  const [searchQuery, setSearchQuery] = useState(mockState === 'search' ? 'sync' : '');
  const [popover, setPopover] = useState<PopoverState | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(mockState === 'delete');
  const [jumpTarget, setJumpTarget] = useState<{ segmentIndex: number } | null>(null);
  const [searchFocusToken, setSearchFocusToken] = useState<number | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);

  const libraryStatus: LibraryStatus =
    mockState === 'upgrading' ? MOCK_STATUS.upgrading : mockState === 'upgrade-failed' ? MOCK_STATUS.failed : MOCK_STATUS.done;

  // Demo state: open the speaker popover on load for `?state=popover`.
  useEffect(() => {
    if (mockState !== 'popover' || !selectedId) return;
    const current = details[selectedId];
    if (current && current.lines[0]) {
      setPopover({ line: current.lines[0], anchor: { top: 250, left: 110 } });
    }
    // Only on first mount for this mock state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visibleMetas = useMemo(() => {
    if (filter.type === 'all') return metas;
    if (filter.type === 'tag') return metas.filter((m) => m.tags.includes(filter.value));
    return metas.filter((m) => m.people.includes(filter.value));
  }, [metas, filter]);

  function select(id: string | null) {
    setSelectedId(id);
    setPopover(null);
    setJumpTarget(null);
  }

  function onSelectFilter(next: SidebarFilter) {
    setFilter(next);
    setSearching(false);
    const list =
      next.type === 'all'
        ? metas
        : metas.filter((m) => (next.type === 'tag' ? m.tags.includes(next.value) : m.people.includes(next.value)));
    select(list[0]?.id ?? null);
  }

  function onSearchChange(value: string) {
    setSearchQuery(value);
    setSearching(value.trim() !== '');
  }

  function onSelectResult(result: SearchResult) {
    setSearching(false);
    setSearchQuery('');
    select(result.meetingId);
    if (result.segmentIndex !== null) setJumpTarget({ segmentIndex: result.segmentIndex });
  }

  function onRenameTitle(title: string) {
    if (!selectedId) return;
    setDetails((prev) => (prev[selectedId] ? { ...prev, [selectedId]: { ...prev[selectedId], title } } : prev));
    setMetas((prev) => prev.map((m) => (m.id === selectedId ? { ...m, title } : m)));
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
    const target = popover.line;
    setDetails((prev) => {
      const current = prev[selectedId];
      if (!current) return prev;
      const lines = current.lines.map((l) => {
        const matches = allMatching ? l.speakerNumber === target.speakerNumber : l.segmentIndex === target.segmentIndex;
        return matches ? { ...l, speakerLabel: name } : l;
      });
      return { ...prev, [selectedId]: { ...current, lines } };
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

  return (
    <GlassPanel width={1040} height={660}>
      <TitleBar title="Meetings" />
      <div className={styles.split} ref={containerRef}>
        <Sidebar
          filters={filters}
          activeFilter={filter}
          activeToday={false}
          onSelectFilter={onSelectFilter}
          onSelectToday={() => {}}
          onConnectClaude={() => {}}
          onOpenSettings={() => {}}
        />

        {searching ? (
          <SearchResults results={mockState === 'search' ? MOCK_RESULTS : []} onSelect={onSelectResult} />
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
            />
          </div>
        )}

        <MeetingDetail
          detail={selectedDetail}
          colorCodeSpeakers={colorCodeSpeakers}
          searchValue={searchQuery}
          onSearchChange={onSearchChange}
          forcedTab={mockState === 'no-summary' ? 'summary' : undefined}
          onRenameTitle={onRenameTitle}
          onSpeakerClick={onSpeakerClick}
          onRequestDelete={() => setConfirmOpen(true)}
          onCopy={() => console.log('copy', selectedId)}
          onExport={() => console.log('export', selectedId)}
          jumpTarget={jumpTarget}
          searchFocusToken={searchFocusToken}
        />

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
