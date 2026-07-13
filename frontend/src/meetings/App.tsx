import { useEffect, useRef, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { ActionButton } from '../components/ActionButton';
import { MEETINGS, speakerColor } from '../mock/meetings';
import type { MeetingDetail, MeetingMeta } from '../mock/meetings';
import { bridge } from '../bridge';
import styles from './App.module.css';

interface MeetingsAppProps {
  colorCodeSpeakers?: boolean;
}

export function MeetingsApp({ colorCodeSpeakers = true }: MeetingsAppProps) {
  const [list, setList] = useState<MeetingMeta[]>(bridge.embedded ? [] : MEETINGS);
  const [selectedId, setSelectedId] = useState<string | null>(bridge.embedded ? null : MEETINGS[0].id);
  const [detail, setDetail] = useState<MeetingDetail | null>(bridge.embedded ? null : MEETINGS[0]);
  const [renaming, setRenaming] = useState(false);
  const [renameText, setRenameText] = useState('');
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const confirmTimer = useRef<number | null>(null);
  const selectedIdRef = useRef<string | null>(bridge.embedded ? null : MEETINGS[0].id);

  function select(id: string | null, metas: MeetingMeta[]) {
    setRenaming(false);
    setConfirmingDelete(false);
    const target = id !== null && metas.some((m) => m.id === id) ? id : metas[0]?.id ?? null;
    selectedIdRef.current = target;
    setSelectedId(target);
    if (!bridge.embedded) {
      setDetail(MEETINGS.find((m) => m.id === target) ?? null);
      return;
    }
    if (target === null) {
      setDetail(null);
      return;
    }
    void bridge.call<MeetingDetail>('meetings.get', { id: target }).then(setDetail).catch(() => setDetail(null));
  }

  function refresh(preserveId: string | null) {
    void bridge.call<MeetingMeta[]>('meetings.list').then((metas) => {
      setList(metas);
      select(preserveId, metas);
    }).catch(() => {});
  }

  useEffect(() => {
    if (!bridge.embedded) return;
    refresh(null);
    return bridge.on('meetings.changed', () => refresh(selectedIdRef.current));
  }, []);

  useEffect(() => {
    return () => {
      if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
    };
  }, []);

  function onDelete() {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
      confirmTimer.current = window.setTimeout(() => setConfirmingDelete(false), 4000);
      return;
    }
    setConfirmingDelete(false);
    if (bridge.embedded && selectedId !== null) {
      void bridge.call<MeetingMeta[]>('meetings.delete', { id: selectedId }).then((metas) => {
        setList(metas);
        select(null, metas);
      });
    } else {
      console.log('delete', selectedId);
    }
  }

  function commitRename() {
    setRenaming(false);
    const title = renameText.trim();
    if (title === '' || selectedId === null) return;
    if (bridge.embedded) {
      void bridge.call<MeetingMeta[]>('meetings.rename', { id: selectedId, title }).then((metas) => {
        setList(metas);
        select(selectedId, metas);
      });
    } else {
      console.log('rename', selectedId, title);
    }
  }

  const selectedMeta = list.find((m) => m.id === selectedId) ?? null;

  function relabelSpeaker(line: MeetingDetail['lines'][number]) {
    if (!bridge.embedded || selectedId === null) return;
    const label = window.prompt('Speaker name', line.speakerLabel)?.trim();
    if (!label) return;
    const allMatching = window.confirm(`Rename every “${line.speakerLabel}” segment?`);
    void bridge.call<MeetingDetail>('meetings.relabelSpeaker', {
      id: selectedId,
      segmentIndex: line.segmentIndex,
      label,
      allMatching,
    }).then(setDetail);
  }

  return (
    <GlassPanel width={720} height={480}>
      <TitleBar title="Meetings" />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Meetings</span>
          {list.map((m) => {
            const active = m.id === selectedId;
            return (
              <button
                key={m.id}
                className={active ? `${styles.item} ${styles.itemActive}` : styles.item}
                onClick={() => select(m.id, list)}
              >
                <div className={active ? `${styles.itemTitle} ${styles.itemTitleActive}` : styles.itemTitle}>
                  {m.title}
                </div>
                <div className={active ? `${styles.itemSub} ${styles.itemSubActive}` : styles.itemSub}>
                  {m.subtitle}
                </div>
              </button>
            );
          })}
        </div>
        <div className={styles.detail}>
          {selectedMeta === null || detail === null ? (
            <div className={styles.empty}>No meetings yet — record one from the dock panel.</div>
          ) : (
            <>
              {renaming ? (
                <input
                  className={styles.renameInput}
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
                <div className={styles.dTitle}>{detail.title}</div>
              )}
              <div className={styles.meta}>
                <span>{detail.date}</span>
                <span className={styles.sep}>•</span>
                <span>{detail.duration}</span>
                <span className={styles.sep}>•</span>
                <span>{detail.speakerCount} {detail.speakerCount === 1 ? 'speaker' : 'speakers'}</span>
              </div>
              <div className={styles.transcript}>
                <div className={styles.lines}>
                  {detail.lines.map((ln, index) => (
                    <div key={index}>
                      <span className={styles.time}>{ln.time}</span>{' '}
                      <span
                        className={styles.speaker}
                        style={{ color: speakerColor(ln.speakerNumber, colorCodeSpeakers) }}
                        title="Click to correct this speaker"
                        onClick={() => relabelSpeaker(ln)}
                      >
                        {ln.speakerLabel}:
                      </span>{' '}
                      <span className={styles.text}>{ln.overlap ? '[overlap] ' : ''}{ln.text}</span>
                    </div>
                  ))}
                </div>
                <div className={styles.fade} />
              </div>
              <div className={styles.actionRow}>
                <ActionButton
                  variant="strong"
                  onClick={() => {
                    if (bridge.embedded && selectedId !== null) void bridge.call('meetings.copy', { id: selectedId });
                    else console.log('copy');
                  }}
                >
                  Copy
                </ActionButton>
                <ActionButton
                  onClick={() => {
                    if (bridge.embedded && selectedId !== null) void bridge.call('meetings.export', { id: selectedId });
                    else console.log('export');
                  }}
                >
                  Export
                </ActionButton>
                <ActionButton
                  onClick={() => {
                    setRenameText(detail.title);
                    setRenaming(true);
                  }}
                >
                  Rename
                </ActionButton>
                <ActionButton variant="danger" className={styles.spacer} onClick={onDelete}>
                  {confirmingDelete ? 'Confirm delete' : 'Delete'}
                </ActionButton>
              </div>
            </>
          )}
        </div>
      </div>
    </GlassPanel>
  );
}
