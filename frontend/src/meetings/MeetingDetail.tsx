import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { MeetingDetail as MeetingDetailType, TranscriptLine } from '../mock/meetings';
import { speakerColor } from '../mock/meetings';
import { ActionButton } from '../components/ActionButton';
import styles from './MeetingDetail.module.css';

type Tab = 'summary' | 'transcript';

interface MeetingDetailProps {
  detail: MeetingDetailType | null;
  colorCodeSpeakers: boolean;
  searchValue: string;
  onSearchChange: (value: string) => void;
  forcedTab?: Tab;
  onRenameTitle: (title: string) => void;
  onSpeakerClick: (line: TranscriptLine, anchor: { top: number; left: number }) => void;
  onRequestDelete: () => void;
  onCopy: () => void;
  onExport: () => void;
  jumpTarget?: { segmentIndex: number } | null;
  searchFocusToken?: number;
}

function initials(name: string): string {
  return name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

export function MeetingDetail({
  detail,
  colorCodeSpeakers,
  searchValue,
  onSearchChange,
  forcedTab,
  onRenameTitle,
  onSpeakerClick,
  onRequestDelete,
  onCopy,
  onExport,
  jumpTarget,
  searchFocusToken,
}: MeetingDetailProps) {
  const [tab, setTab] = useState<Tab>('summary');
  const [renaming, setRenaming] = useState(false);
  const [renameText, setRenameText] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState('');
  const [searchDraft, setSearchDraft] = useState(searchValue);
  const searchTimer = useRef<number | null>(null);
  const titleRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

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
    setFindOpen(false);
    setFindQuery('');
  }, [detail?.id, forcedTab]);

  useEffect(() => {
    setSearchDraft(searchValue);
  }, [searchValue]);

  function onSearchInput(value: string) {
    setSearchDraft(value);
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => onSearchChange(value), 250);
  }

  function commitRename() {
    const title = renameText.trim();
    setRenaming(false);
    if (title !== '' && detail && title !== detail.title) onRenameTitle(title);
  }

  function onTranscriptKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
      e.preventDefault();
      setFindOpen(true);
    }
  }

  const findMatches: { segmentIndex: number }[] = [];
  if (detail && findQuery.trim() !== '') {
    const q = findQuery.trim().toLowerCase();
    detail.lines.forEach((line) => {
      if (line.text.toLowerCase().includes(q)) findMatches.push({ segmentIndex: line.segmentIndex });
    });
  }

  return (
    <div className={styles.detail}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarSpacer} />
        <div className={styles.searchWrap}>
          <input
            ref={searchInputRef}
            className={styles.searchInput}
            placeholder="Search meetings"
            aria-label="Search meetings"
            value={searchDraft}
            onChange={(e) => onSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setSearchDraft('');
                onSearchChange('');
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
              e.preventDefault();
              onRequestDelete();
            }
          }}
        >
          <div className={styles.header}>
            {renaming ? (
              <input
                className={styles.titleInput}
                value={renameText}
                autoFocus
                onChange={(e) => setRenameText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitRename();
                  if (e.key === 'Escape') setRenaming(false);
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
              {detail.event && (
                <>
                  <span className={styles.sep}>·</span>
                  <button className={styles.eventChip} title={`Linked to ${detail.event.title}`}>
                    📅 {detail.event.title} ⌄
                  </button>
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

            {detail.approximate && <div className={styles.approxNote}>≈ approximate times</div>}
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
                  <p className={styles.summaryText}>{detail.summary}</p>
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
                  <p>No summary yet. Ask Claude to summarise this meeting.</p>
                  <ActionButton
                    onClick={() => {
                      const prompt = `Summarise and tag my Speakeasy meeting "${detail.title}" (${detail.id}) and save the notes.`;
                      void navigator.clipboard?.writeText(prompt).catch(() => {});
                    }}
                  >
                    Copy prompt
                  </ActionButton>
                </div>
              )}
            </div>
          ) : (
            <div
              ref={transcriptRef}
              className={styles.transcriptPane}
              tabIndex={0}
              onKeyDown={onTranscriptKeyDown}
            >
              {findOpen && (
                <div className={styles.findBar}>
                  <input
                    className={styles.findInput}
                    autoFocus
                    placeholder="Find in transcript"
                    aria-label="Find in transcript"
                    value={findQuery}
                    onChange={(e) => setFindQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') {
                        setFindOpen(false);
                        setFindQuery('');
                      }
                    }}
                  />
                  <span className={styles.findCount}>
                    {findMatches.length > 0 ? `1 of ${findMatches.length}` : '0 of 0'}
                  </span>
                  <button className={styles.findNav} aria-label="Previous match">‹</button>
                  <button className={styles.findNav} aria-label="Next match">›</button>
                  <button
                    className={styles.findDone}
                    onClick={() => {
                      setFindOpen(false);
                      setFindQuery('');
                    }}
                  >
                    Done
                  </button>
                </div>
              )}
              <div className={styles.lines}>
                {detail.lines.map((line) => {
                  const highlighted = jumpTarget?.segmentIndex === line.segmentIndex;
                  return (
                    <div
                      key={line.segmentIndex}
                      className={highlighted ? `${styles.line} ${styles.lineHighlight}` : styles.line}
                    >
                      <span className={styles.time}>{line.time}</span>{' '}
                      <button
                        className={styles.speaker}
                        style={{ color: speakerColor(line.speakerNumber, colorCodeSpeakers) }}
                        onClick={(e) => {
                          const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                          onSpeakerClick(line, { top: rect.bottom, left: rect.left });
                        }}
                      >
                        {line.speakerLabel}:
                      </button>{' '}
                      <span className={styles.text}>
                        {line.overlap ? '[overlap] ' : ''}
                        {line.text}
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
            <div className={styles.menuWrap}>
              <button
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
