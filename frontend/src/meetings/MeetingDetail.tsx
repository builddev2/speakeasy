import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { EventChip, MeetingDetail as MeetingDetailType, SummaryBlock, TranscriptLine } from '../mock/meetings';
import { speakerColor } from '../mock/meetings';
import { ActionButton } from '../components/ActionButton';
import styles from './MeetingDetail.module.css';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import { useOverlayEscape } from './overlayStack';

type Tab = 'summary' | 'transcript';

export interface JumpTarget {
  /** The meeting this jump targets — the effect waits for `detail` to catch
   * up to this id before consuming the jump (embedded mode fetches detail
   * asynchronously via meetings.get, so it isn't cached yet when the search
   * result is chosen). */
  meetingId: string;
  segmentIndex: number | null;
  seconds: number | null;
  kind: 'transcript' | 'notes';
  /** Bumped on every selection so re-jumping to the same line still fires. */
  nonce: number;
}

interface MeetingDetailProps {
  detail: MeetingDetailType | null;
  colorCodeSpeakers: boolean;
  searchValue: string;
  onSearchChange: (value: string) => void;
  forcedTab?: Tab;
  autoOpenPopover?: boolean;
  onRenameTitle: (title: string) => void;
  onSpeakerClick: (line: TranscriptLine, anchor: { top: number; left: number }) => void;
  onRequestDelete: () => void;
  onCopy: () => void;
  onExport: () => void;
  onRequestSummary: () => void;
  jumpTarget?: JumpTarget | null;
  searchFocusToken?: number;
  /** Same-day events the meeting can be linked to; the chip menu fetches on open. */
  onLoadEvents?: () => Promise<EventChip[]>;
  /** key null unlinks. Absent when Calendar isn't available: no chip controls. */
  onLinkEvent?: (key: string | null) => void;
}

interface FindMatch {
  segmentIndex: number;
  partIndex: number;
}

function SummaryBlocks({ blocks }: { blocks: SummaryBlock[] }) {
  return (
    <div className={styles.summaryBody}>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case 'tldr':
            return <p key={i} className={styles.summaryTldr}>{b.text}</p>;
          case 'heading':
            return <h3 key={i} className={styles.summaryHeading}>{b.text}</h3>;
          case 'para':
            return <p key={i} className={styles.summaryPara}>{b.text}</p>;
          case 'bullets':
            return (
              <ul key={i} className={styles.summaryList}>
                {b.items.map((item, j) => (
                  <li key={j}><span className={styles.summaryDot} aria-hidden="true" />{item}</li>
                ))}
              </ul>
            );
          default: {
            const _exhaustive: never = b;
            return _exhaustive;
          }
        }
      })}
    </div>
  );
}

function initials(name: string): string {
  return name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

/** Splits `text` into plain/hit parts for every case-insensitive occurrence of `query`. */
function splitHighlights(text: string, query: string): { text: string; hit: boolean }[] {
  if (!query) return [{ text, hit: false }];
  const lower = text.toLowerCase();
  const q = query.toLowerCase();
  const parts: { text: string; hit: boolean }[] = [];
  let i = 0;
  while (i < text.length) {
    const idx = lower.indexOf(q, i);
    if (idx === -1) {
      parts.push({ text: text.slice(i), hit: false });
      break;
    }
    if (idx > i) parts.push({ text: text.slice(i, idx), hit: false });
    parts.push({ text: text.slice(idx, idx + q.length), hit: true });
    i = idx + q.length;
  }
  return parts;
}

export function MeetingDetail({
  detail,
  colorCodeSpeakers,
  searchValue,
  onSearchChange,
  forcedTab,
  autoOpenPopover,
  onRenameTitle,
  onSpeakerClick,
  onRequestDelete,
  onCopy,
  onExport,
  onRequestSummary,
  jumpTarget,
  searchFocusToken,
  onLoadEvents,
  onLinkEvent,
}: MeetingDetailProps) {
  const [tab, setTab] = useState<Tab>('summary');
  const [renaming, setRenaming] = useState(false);
  const [renameText, setRenameText] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [eventMenuOpen, setEventMenuOpen] = useState(false);
  const [eventChoices, setEventChoices] = useState<EventChip[]>([]);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState('');
  const [findIndex, setFindIndex] = useState(0);
  const [highlightSegment, setHighlightSegment] = useState<number | null>(null);
  const [searchDraft, setSearchDraft] = useState(searchValue);
  const searchTimer = useRef<number | null>(null);
  const highlightTimer = useRef<number | null>(null);
  const autoOpenedRef = useRef(false);
  const consumedJumpNonceRef = useRef<number | null>(null);
  const titleRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const menuWrapRef = useRef<HTMLDivElement>(null);
  const eventListRef = useRef<HTMLDivElement>(null);
  const [eventListMax, setEventListMax] = useState<number | undefined>(undefined);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const eventWrapRef = useRef<HTMLSpanElement>(null);
  const eventButtonRef = useRef<HTMLButtonElement>(null);
  const lineRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const speakerRefs = useRef<Map<number, HTMLButtonElement>>(new Map());

  useEffect(() => {
    if (searchFocusToken === undefined) return;
    searchInputRef.current?.focus();
    // Only react to the token changing, not the initial render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchFocusToken]);

  useEffect(() => {
    if (!detail) return;
    setTab(forcedTab ?? (detail.summary ? 'summary' : 'transcript'));
    setRenaming(false);
    setMenuOpen(false);
    setEventMenuOpen(false);
    setFindOpen(false);
    setFindQuery('');
    setFindIndex(0);
    autoOpenedRef.current = false;
    setHighlightSegment(null);
    if (highlightTimer.current !== null) {
      window.clearTimeout(highlightTimer.current);
      highlightTimer.current = null;
    }
  }, [detail?.id, forcedTab]);

  useEffect(() => {
    setSearchDraft(searchValue);
  }, [searchValue]);

  // Search → Transcript jump: switch tab (Summary for notes hits), scroll the
  // matching line into view (or the nearest by `start` when there's no exact
  // segment). The 1.2s flash highlight is skipped entirely under Reduce Motion
  // rather than just losing its transition, since it's a motion effect, not a
  // static state.
  //
  // In embedded mode `detail` starts out null/stale — App only fetches it
  // (meetings.get) after setting selectedId — so this effect must also
  // re-run once `detail` catches up to `jumpTarget.meetingId`, not just when
  // `jumpTarget` itself changes. `consumedJumpNonceRef` stops it from
  // re-firing (re-scrolling/re-highlighting) on every later detail update
  // for the same jump.
  useEffect(() => {
    if (!jumpTarget || !detail) return;
    if (detail.id !== jumpTarget.meetingId) return;
    if (consumedJumpNonceRef.current === jumpTarget.nonce) return;
    consumedJumpNonceRef.current = jumpTarget.nonce;
    if (jumpTarget.kind === 'notes') {
      setTab('summary');
      return;
    }
    setTab('transcript');
    let targetIndex = jumpTarget.segmentIndex;
    if (targetIndex === null && jumpTarget.seconds !== null) {
      let best: TranscriptLine | null = null;
      for (const line of detail.lines) {
        if (best === null || Math.abs(line.start - jumpTarget.seconds) < Math.abs(best.start - jumpTarget.seconds)) {
          best = line;
        }
      }
      targetIndex = best?.segmentIndex ?? null;
    }
    if (targetIndex === null) return;
    const resolvedIndex = targetIndex;
    const raf = window.requestAnimationFrame(() => {
      lineRefs.current
        .get(resolvedIndex)
        ?.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
    });
    if (!prefersReducedMotion()) {
      setHighlightSegment(resolvedIndex);
      if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
      highlightTimer.current = window.setTimeout(() => setHighlightSegment(null), 1200);
    }
    return () => window.cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpTarget, detail?.id]);

  // V1 demo state: open the popover on the first real speaker button so the
  // anchor matches an actual click, instead of a hard-coded position.
  useEffect(() => {
    if (!autoOpenPopover || tab !== 'transcript' || !detail || detail.lines.length === 0) return;
    if (autoOpenedRef.current) return;
    const first = detail.lines[0];
    const raf = window.requestAnimationFrame(() => {
      const btn = speakerRefs.current.get(first.segmentIndex);
      if (btn) {
        const rect = btn.getBoundingClientRect();
        onSpeakerClick(first, { top: rect.bottom, left: rect.left });
        autoOpenedRef.current = true;
      }
    });
    return () => window.cancelAnimationFrame(raf);
  }, [autoOpenPopover, tab, detail, onSpeakerClick]);

  useEffect(() => {
    if (!menuOpen) return;
    function onDocMouseDown(e: MouseEvent) {
      if (menuWrapRef.current && !menuWrapRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocMouseDown);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown);
    };
  }, [menuOpen]);

  useOverlayEscape(menuOpen, () => {
    setMenuOpen(false);
    moreButtonRef.current?.focus();
  });

  // Fetched on each open so the list matches the meeting's day right now.
  useEffect(() => {
    if (!eventMenuOpen || !onLoadEvents) return;
    let cancelled = false;
    onLoadEvents()
      .then((list) => {
        if (!cancelled) setEventChoices(list);
      })
      .catch(() => {
        if (!cancelled) setEventChoices([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventMenuOpen, detail?.id]);

  // Cap the list to the window and show the linked event; Unlink sits above it.
  useLayoutEffect(() => {
    if (!eventMenuOpen) return;
    const list = eventListRef.current;
    if (!list) return;
    const top = list.getBoundingClientRect().top;
    setEventListMax(Math.max(96, window.innerHeight - top - 12 - 6));
    list.querySelector('[aria-checked="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [eventMenuOpen, eventChoices]);

  useEffect(() => {
    if (!eventMenuOpen) return;
    function onDocMouseDown(e: MouseEvent) {
      if (eventWrapRef.current && !eventWrapRef.current.contains(e.target as Node)) {
        setEventMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [eventMenuOpen]);

  useOverlayEscape(eventMenuOpen, () => {
    setEventMenuOpen(false);
    eventButtonRef.current?.focus();
  });

  useOverlayEscape(renaming, () => setRenaming(false));
  useOverlayEscape(findOpen, closeFindBar);

  function onSearchInput(value: string) {
    setSearchDraft(value);
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => onSearchChange(value), 250);
  }

  function clearSearch() {
    if (searchTimer.current !== null) {
      window.clearTimeout(searchTimer.current);
      searchTimer.current = null;
    }
    setSearchDraft('');
    onSearchChange('');
  }

  function commitRename() {
    const title = renameText.trim();
    setRenaming(false);
    if (title !== '' && detail && title !== detail.title) onRenameTitle(title);
  }

  // ⌘F opens find-in-transcript from anywhere in the detail column (title,
  // toolbar, summary, transcript...). MeetingList handles its own ⌘F to focus
  // the toolbar search field instead, since the two components never share a
  // keydown target.
  function onDetailKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
      if (!detail) return;
      e.preventDefault();
      setTab('transcript');
      setFindOpen(true);
    }
  }

  function closeFindBar() {
    setFindOpen(false);
    setFindQuery('');
    transcriptRef.current?.focus();
  }

  const findMatches: FindMatch[] = [];
  const lineParts = new Map<number, { text: string; hit: boolean }[]>();
  if (detail && findOpen && findQuery.trim() !== '') {
    const q = findQuery.trim().toLowerCase();
    detail.lines.forEach((line) => {
      const parts = splitHighlights(line.text, q);
      lineParts.set(line.segmentIndex, parts);
      parts.forEach((part, partIndex) => {
        if (part.hit) findMatches.push({ segmentIndex: line.segmentIndex, partIndex });
      });
    });
  }
  const currentMatch = findMatches[findIndex] ?? null;
  const currentMatchSegment = currentMatch?.segmentIndex ?? null;

  function goToMatch(delta: number) {
    if (findMatches.length === 0) return;
    setFindIndex((i) => (i + delta + findMatches.length) % findMatches.length);
  }

  useEffect(() => {
    if (!findOpen || currentMatchSegment === null) return;
    lineRefs.current
      .get(currentMatchSegment)
      ?.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }, [findOpen, currentMatchSegment]);

  return (
    <div className={styles.detail} onKeyDown={onDetailKeyDown}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarSpacer} />
        <div className={styles.searchWrap}>
          <input
            ref={searchInputRef}
            {...NO_AUTOCORRECT}
            className={styles.searchInput}
            placeholder="Search meetings"
            aria-label="Search meetings"
            value={searchDraft}
            onChange={(e) => onSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                clearSearch();
              }
            }}
          />
        </div>
      </div>

      {!detail ? (
        <div className={styles.empty}>Select a meeting to see its summary and transcript.</div>
      ) : (
        <div
          className={styles.body}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Backspace') {
              if (isEditableTarget(e.target)) return;
              e.preventDefault();
              onRequestDelete();
            }
          }}
        >
          <div className={styles.header}>
            {renaming ? (
              <input
                className={styles.titleInput}
                {...NO_AUTOCORRECT}
                value={renameText}
                autoFocus
                onChange={(e) => setRenameText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitRename();
                }}
                onBlur={() => setRenaming(false)}
              />
            ) : (
              <div
                ref={titleRef}
                className={styles.title}
                tabIndex={0}
                role="button"
                aria-label={`Rename meeting ${detail.title}`}
                onDoubleClick={() => {
                  setRenameText(detail.title);
                  setRenaming(true);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    setRenameText(detail.title);
                    setRenaming(true);
                  }
                }}
              >
                {detail.title}
              </div>
            )}

            <div className={styles.metaLine}>
              <span>{detail.date}</span>
              <span className={styles.sep}>·</span>
              <span>{detail.time}</span>
              <span className={styles.sep}>·</span>
              <span>{detail.duration}</span>
              {(detail.event || onLinkEvent) && (
                <>
                  <span className={styles.sep}>·</span>
                  <span className={styles.eventWrap} ref={eventWrapRef}>
                    {detail.event ? (
                      <button
                        ref={eventButtonRef}
                        className={styles.eventChip}
                        title={`Linked to ${detail.event.title}`}
                        aria-haspopup={onLinkEvent ? 'menu' : undefined}
                        aria-expanded={onLinkEvent ? eventMenuOpen : undefined}
                        onClick={() => onLinkEvent && setEventMenuOpen((v) => !v)}
                      >
                        <span className={styles.eventIcon} aria-hidden="true">
                          📅
                        </span>
                        <span className={styles.eventTitle}>{detail.event.title}</span>
                        <span className={styles.eventChevron} aria-hidden="true">
                          ⌄
                        </span>
                      </button>
                    ) : (
                      <button
                        ref={eventButtonRef}
                        className={styles.linkEventButton}
                        aria-haspopup="menu"
                        aria-expanded={eventMenuOpen}
                        onClick={() => setEventMenuOpen((v) => !v)}
                      >
                        Link to Event…
                      </button>
                    )}
                    {eventMenuOpen && onLinkEvent && (
                      <div className={styles.eventMenu} role="menu">
                        {detail.event && (
                          <>
                            <button
                              role="menuitem"
                              className={styles.menuItem}
                              onClick={() => {
                                setEventMenuOpen(false);
                                onLinkEvent(null);
                              }}
                            >
                              Unlink
                            </button>
                            <div className={styles.eventMenuDivider} role="separator" />
                            <div className={styles.eventMenuHeader}>{detail.event.time}</div>
                          </>
                        )}
                        <div
                          className={styles.eventMenuList}
                          ref={eventListRef}
                          style={eventListMax ? { maxHeight: eventListMax } : undefined}
                        >
                          {eventChoices.map((choice) => {
                            const current = detail.event?.key === choice.key;
                            return (
                              <button
                                key={choice.key}
                                role="menuitemradio"
                                aria-checked={current}
                                className={styles.menuItem}
                                onClick={() => {
                                  setEventMenuOpen(false);
                                  if (!current) onLinkEvent(choice.key);
                                }}
                              >
                                <span className={styles.eventCheck} aria-hidden="true">
                                  {current ? '✓' : ''}
                                </span>
                                {choice.time} · {choice.title}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </span>
                </>
              )}
            </div>

            <div className={styles.peopleTagsRow}>
              <div className={styles.people} title={detail.people.join(', ')}>
                {detail.people.slice(0, 4).map((person) => (
                  <span key={person} className={styles.avatar}>
                    {initials(person)}
                  </span>
                ))}
                {detail.people.length > 4 && (
                  <span className={styles.avatarMore}>+{detail.people.length - 4}</span>
                )}
              </div>
              {detail.tags.length > 0 && (
                <div className={styles.tags}>
                  {detail.tags.map((tag) => (
                    <span key={tag} className={styles.tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {detail.approximate && (
              <div
                className={styles.approxNote}
                title="Imported from an older version; times are estimated from when processing finished."
              >
                ≈ approximate times
              </div>
            )}
          </div>

          <div className={styles.segmented} role="tablist" aria-label="Summary or transcript">
            <button
              role="tab"
              aria-selected={tab === 'summary'}
              className={tab === 'summary' ? `${styles.segment} ${styles.segmentActive}` : styles.segment}
              onClick={() => setTab('summary')}
            >
              Summary
            </button>
            <button
              role="tab"
              aria-selected={tab === 'transcript'}
              className={tab === 'transcript' ? `${styles.segment} ${styles.segmentActive}` : styles.segment}
              onClick={() => setTab('transcript')}
            >
              Transcript
            </button>
          </div>

          {tab === 'summary' ? (
            <div className={styles.summaryPane}>
              {detail.summary ? (
                <>
                  {detail.summaryQueued && (
                    <div className={styles.queuedNote}>Queued for a new summary. Claude checks every 30 minutes while the Claude app is open.</div>
                  )}
                  <SummaryBlocks blocks={detail.summaryBlocks} />
                  {detail.actionItems.length > 0 && (
                    <>
                      <div className={styles.actionItemsHeading}>Action items</div>
                      <ul className={styles.actionItems}>
                        {detail.actionItems.map((item, i) => (
                          <li key={i}>
                            <span className={styles.actionDot} aria-hidden="true" />
                            {item}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              ) : (
                <div className={styles.noSummary}>
                  <p>{detail.summaryQueued
                        ? 'Queued — Claude will summarise this the next time it checks (every 30 minutes while the Claude app is open).'
                        : 'No summary yet.'}</p>
                  <ActionButton disabled={detail.summaryQueued} onClick={onRequestSummary}>
                    Summarise
                  </ActionButton>
                </div>
              )}
            </div>
          ) : (
            <div ref={transcriptRef} className={styles.transcriptPane} tabIndex={0}>
              {findOpen && (
                <div className={styles.findBar}>
                  <input
                    className={styles.findInput}
                    {...NO_AUTOCORRECT}
                    autoFocus
                    placeholder="Find in transcript"
                    aria-label="Find in transcript"
                    value={findQuery}
                    onChange={(e) => {
                      setFindQuery(e.target.value);
                      setFindIndex(0);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        goToMatch(e.shiftKey ? -1 : 1);
                      }
                    }}
                  />
                  <span className={styles.findCount}>
                    {findMatches.length > 0 ? `${findIndex + 1} of ${findMatches.length}` : '0 of 0'}
                  </span>
                  <button className={styles.findNav} aria-label="Previous match" onClick={() => goToMatch(-1)}>
                    ‹
                  </button>
                  <button className={styles.findNav} aria-label="Next match" onClick={() => goToMatch(1)}>
                    ›
                  </button>
                  <button className={styles.findDone} onClick={closeFindBar}>
                    Done
                  </button>
                </div>
              )}
              <div className={styles.lines}>
                {detail.lines.map((line) => {
                  const isJumpHighlight = highlightSegment === line.segmentIndex;
                  const parts = lineParts.get(line.segmentIndex);
                  const lineClass = isJumpHighlight ? `${styles.line} ${styles.lineHighlight}` : styles.line;
                  return (
                    <div
                      key={line.segmentIndex}
                      ref={(el) => {
                        if (el) lineRefs.current.set(line.segmentIndex, el);
                        else lineRefs.current.delete(line.segmentIndex);
                      }}
                      className={lineClass}
                    >
                      <span className={styles.lineHead}>
                        <span className={styles.time}>
                          {detail.approximate ? '≈' : ''}
                          {line.time}
                        </span>
                        <button
                          ref={(el) => {
                            if (el) speakerRefs.current.set(line.segmentIndex, el);
                            else speakerRefs.current.delete(line.segmentIndex);
                          }}
                          className={styles.speaker}
                          style={{ color: speakerColor(line.speakerNumber, colorCodeSpeakers) }}
                          onClick={(e) => {
                            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                            onSpeakerClick(line, { top: rect.bottom, left: rect.left });
                          }}
                        >
                          {line.speakerLabel}:
                        </button>
                      </span>
                      <span className={styles.lineText}>
                        {line.overlap ? '[overlap] ' : ''}
                        {parts
                          ? parts.map((part, partIndex) =>
                              part.hit ? (
                                <mark
                                  key={partIndex}
                                  className={
                                    currentMatch?.segmentIndex === line.segmentIndex && currentMatch.partIndex === partIndex
                                      ? `${styles.mark} ${styles.markCurrent}`
                                      : styles.mark
                                  }
                                >
                                  {part.text}
                                </mark>
                              ) : (
                                <span key={partIndex}>{part.text}</span>
                              ),
                            )
                          : line.text}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className={styles.actionsRow}>
            <ActionButton variant="strong" onClick={onCopy}>
              Copy
            </ActionButton>
            <ActionButton onClick={onExport}>Export</ActionButton>
            <div className={styles.menuWrap} ref={menuWrapRef}>
              <button
                ref={moreButtonRef}
                className={styles.moreButton}
                aria-label="More actions"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                onClick={() => setMenuOpen((v) => !v)}
              >
                ···
              </button>
              {menuOpen && (
                <div className={styles.menu} role="menu">
                  <button
                    role="menuitem"
                    className={styles.menuItem}
                    onClick={() => {
                      setMenuOpen(false);
                      setRenameText(detail.title);
                      setRenaming(true);
                    }}
                  >
                    Rename…
                  </button>
                  {detail.summary && !detail.summaryQueued && (
                    <button
                      role="menuitem"
                      className={styles.menuItem}
                      onClick={() => {
                        setMenuOpen(false);
                        onRequestSummary();
                      }}
                    >
                      Redo summary
                    </button>
                  )}
                  <button
                    role="menuitem"
                    className={`${styles.menuItem} ${styles.menuItemDanger}`}
                    onClick={() => {
                      setMenuOpen(false);
                      onRequestDelete();
                    }}
                  >
                    Delete…
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
