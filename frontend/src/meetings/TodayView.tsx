import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { AgendaEvent } from '../mock/meetings';
import { ActionButton } from '../components/ActionButton';
import { EmptyState } from './EmptyState';
import styles from './TodayView.module.css';

export type TodayConnection = 'connected' | 'denied' | 'unconnected';

interface TodayViewProps {
  connection: TodayConnection;
  agenda: AgendaEvent[];
  onSelectMeeting: (meetingId: string) => void;
  onRecord: (key: string) => void;
  onConnectCalendar: () => void;
  onOpenPrivacySettings: () => void;
}

function peopleLabel(count: number): string {
  return count === 1 ? '1 person' : `${count} people`;
}

/**
 * A thin red "now" line separates past rows from the current/upcoming ones.
 * Derived from the agenda itself (the first row that isn't already
 * `recorded`), never a hand-picked index.
 */
function nowLineIndex(agenda: AgendaEvent[]): number {
  const index = agenda.findIndex((event) => event.status !== 'recorded');
  return index === -1 ? agenda.length : index;
}

function statusCell(event: AgendaEvent, onRecord: (key: string) => void): ReactNode {
  if (event.status === 'recorded') {
    return <span className={styles.recordedLabel}>Recorded ✓</span>;
  }
  if (event.status === 'recording') {
    return (
      <span className={styles.recordingLabel}>
        <span className={styles.recordingDot} aria-hidden="true" />
        Recording…
      </span>
    );
  }
  if (event.status === 'record') {
    return (
      <button
        className={styles.recordButton}
        onClick={(e) => {
          e.stopPropagation();
          onRecord(event.key);
        }}
      >
        Record
      </button>
    );
  }
  return null;
}

export function TodayView({
  connection,
  agenda,
  onSelectMeeting,
  onRecord,
  onConnectCalendar,
  onOpenPrivacySettings,
}: TodayViewProps) {
  const nowIndex = useMemo(() => nowLineIndex(agenda), [agenda]);

  if (connection === 'unconnected') {
    return (
      <div className={styles.view}>
        <div className={styles.header}>
          <h1 className={styles.title}>Today</h1>
        </div>
        <EmptyState
          title="See your meetings here."
          body="Speakeasy reads Calendar on this Mac to title recordings and offer to record. Nothing leaves your Mac."
          action={
            <ActionButton variant="strong" onClick={onConnectCalendar}>
              Connect Calendar
            </ActionButton>
          }
        />
      </div>
    );
  }

  if (connection === 'denied') {
    return (
      <div className={styles.view}>
        <div className={styles.header}>
          <h1 className={styles.title}>Today</h1>
        </div>
        <EmptyState
          title="Calendar access is off."
          action={
            <ActionButton variant="strong" onClick={onOpenPrivacySettings}>
              Open Privacy Settings
            </ActionButton>
          }
        />
      </div>
    );
  }

  return (
    <div className={styles.view}>
      <div className={styles.header}>
        <h1 className={styles.title}>Today</h1>
      </div>
      {agenda.length === 0 ? (
        <EmptyState title="Nothing on your calendar today." />
      ) : (
        <div className={styles.agenda} role="list" aria-label="Today's agenda">
          {agenda.map((event, index) => (
            <div key={event.key} className={styles.rowWrap}>
              {index === nowIndex && <div className={styles.nowLine} aria-hidden="true" />}
              {event.status === 'recorded' ? (
                <button
                  className={styles.row}
                  role="listitem"
                  onClick={() => event.meetingId && onSelectMeeting(event.meetingId)}
                >
                  <span className={styles.time}>{event.time}</span>
                  <span className={styles.eventTitle}>{event.title}</span>
                  <span className={styles.attendees}>{peopleLabel(event.attendeeCount)}</span>
                  <span className={styles.statusCell}>{statusCell(event, onRecord)}</span>
                </button>
              ) : (
                <div className={styles.row} role="listitem">
                  <span className={styles.time}>{event.time}</span>
                  <span className={styles.eventTitle}>{event.title}</span>
                  <span className={styles.attendees}>{peopleLabel(event.attendeeCount)}</span>
                  <span className={styles.statusCell}>{statusCell(event, onRecord)}</span>
                </div>
              )}
            </div>
          ))}
          {nowIndex === agenda.length && <div className={styles.nowLine} aria-hidden="true" />}
        </div>
      )}
    </div>
  );
}
