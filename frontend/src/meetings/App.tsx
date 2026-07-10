import { useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { ActionButton } from '../components/ActionButton';
import { MEETINGS, speakerColor } from '../mock/meetings';
import styles from './App.module.css';

interface MeetingsAppProps {
  colorCodeSpeakers?: boolean;
}

export function MeetingsApp({ colorCodeSpeakers = true }: MeetingsAppProps) {
  const [selectedMeetingId, setSelectedMeetingId] = useState(MEETINGS[0].id);
  const meeting = MEETINGS.find((m) => m.id === selectedMeetingId) ?? MEETINGS[0];

  return (
    <GlassPanel width={720} height={480}>
      <TitleBar title="Meetings" />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Meetings</span>
          {MEETINGS.map((m) => {
            const active = m.id === selectedMeetingId;
            return (
              <button
                key={m.id}
                className={active ? `${styles.item} ${styles.itemActive}` : styles.item}
                onClick={() => setSelectedMeetingId(m.id)}
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
          <div className={styles.dTitle}>{meeting.title}</div>
          <div className={styles.meta}>
            <span>{meeting.date}</span>
            <span className={styles.sep}>•</span>
            <span>{meeting.duration}</span>
            <span className={styles.sep}>•</span>
            <span>{meeting.speakerCount} speakers</span>
          </div>
          <div className={styles.transcript}>
            <div className={styles.lines}>
              {meeting.lines.map((ln, index) => {
                const speakerNumber = Number(ln.speaker.replace(/\D/g, ''));
                return (
                  <div key={index}>
                    <span className={styles.time}>{ln.time}</span>{' '}
                    <span className={styles.speaker} style={{ color: speakerColor(speakerNumber, colorCodeSpeakers) }}>
                      {ln.speaker}
                    </span>{' '}
                    <span className={styles.text}>{ln.text}</span>
                  </div>
                );
              })}
            </div>
            <div className={styles.fade} />
          </div>
          <div className={styles.actionRow}>
            <ActionButton variant="strong" onClick={() => console.log('copy')}>Copy</ActionButton>
            <ActionButton onClick={() => console.log('export')}>Export</ActionButton>
            <ActionButton onClick={() => console.log('rename')}>Rename</ActionButton>
            <ActionButton variant="danger" className={styles.spacer} onClick={() => console.log('delete')}>
              Delete
            </ActionButton>
          </div>
        </div>
      </div>
    </GlassPanel>
  );
}
