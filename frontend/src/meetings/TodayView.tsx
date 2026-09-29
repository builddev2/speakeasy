import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { AgendaEvent } from '../mock/meetings';
import { ActionButton } from '../components/ActionButton';
import { EmptyState } from './EmptyState';
import styles from './TodayView.module.css';

export type TodayConnection = 'connected' | 'denied' | 'unconnected';

interface UpcomingDay {
  dayLabel: string;
  events: AgendaEvent[];
}

interface TodayViewProps {
  connection: TodayConnection;
  agenda: AgendaEvent[];
  upcoming: UpcomingDay[];
  onSelectMeeting: (meetingId: string) => void;
  onRecord: (key: string) => void;
  onConnectCalendar: () => void;
  onOpenPrivacySettings: () => void;
}

// A fixed mock "now" (4:29 PM) — it sits between the Design Sync row that's
// still recording (started 3:30) and the 4:30 Roadmap Review Record row, so
// both make sense next to the now-line.
const MOCK_NOW_MINUTES = 16 * 60 + 29;

function peopleLabel(count: number): string {
  return count === 1 ? '1 person' : `${count} people`;
}

function eventsLabel(count: number): string {
  return count === 1 ? '1 event' : `${count} events`;
}

function parseTimeToMinutes(time: string): number {
  const match = /^(\d{1,2}):(\d{2})\s*(AM|PM)$/i.exec(time.trim());
  if (!match) return 0;
  let hours = Number(match[1]) % 12;
  if (/pm/i.test(match[3])) hours += 12;
  return hours * 60 + Number(match[2]);
}

/**
 * The "now" line's position, derived by comparing each event's start time
 * against the mock "now" — not a status heuristic — so it lands wherever the
 * agenda's own times say it should.
 */
function nowLineIndex(agenda: AgendaEvent[], nowMinutes: number): number {
  const index = agenda.findIndex((event) => parseTimeToMinutes(event.time) > nowMinutes);
  return index === -1 ? agenda.length : index;
}

function timeRange(event: AgendaEvent): string {
  return event.endTime ? `${event.time}–${event.endTime}` : event.time;
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
  upcoming,
  onSelectMeeting,
  onRecord,
  onConnectCalendar,
  onOpenPrivacySettings,
}: TodayViewProps) {
  const nowIndex = useMemo(() => nowLineIndex(agenda, MOCK_NOW_MINUTES), [agenda]);
  const [openDays, setOpenDays] = useState<Set<number>>(new Set());

  function toggleDay(index: number) {
    setOpenDays((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

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
            <div key={event.key} className={styles.rowWrap} role="listitem">
              {index === nowIndex && <div className={styles.nowLine} aria-hidden="true" />}
              {event.status === 'recorded' ? (
                <button
                  className={styles.row}
                  onClick={() => event.meetingId && onSelectMeeting(event.meetingId)}
                >
                  <span className={styles.time}>{timeRange(event)}</span>
                  <span className={styles.eventTitle}>{event.title}</span>
                  <span className={styles.attendees}>{peopleLabel(event.attendeeCount)}</span>
                  <span className={styles.statusCell}>{statusCell(event, onRecord)}</span>
                </button>
              ) : (
                <div className={styles.row}>
                  <span className={styles.time}>{timeRange(event)}</span>
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

      {upcoming.length > 0 && (
        <div className={styles.upcoming}>
          <div className={styles.upcomingHeading}>Upcoming</div>
          {upcoming.map((day, dayIndex) => {
            const open = openDays.has(dayIndex);
            const panelId = `today-upcoming-panel-${dayIndex}`;
            return (
              <div key={day.dayLabel} className={styles.upcomingDay}>
                <button
                  className={styles.disclosureRow}
                  aria-expanded={open}
                  aria-controls={panelId}
                  onClick={() => toggleDay(dayIndex)}
                >
                  <span className={open ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} aria-hidden="true">
                    ›
                  </span>
                  {day.dayLabel} · {eventsLabel(day.events.length)}
                </button>
                {open && (
                  <div id={panelId} className={styles.upcomingList} role="list" aria-label={`${day.dayLabel} agenda`}>
                    {day.events.map((event) => (
                      <div key={event.key} className={styles.upcomingRow} role="listitem">
                        <span className={styles.time}>{timeRange(event)}</span>
                        <span className={styles.eventTitle}>{event.title}</span>
                        <span className={styles.attendees}>{peopleLabel(event.attendeeCount)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
