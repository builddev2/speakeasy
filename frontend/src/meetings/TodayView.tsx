import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { AgendaEvent, CalendarAccess, RecordingInfo, UpcomingDay } from '../mock/meetings';
import { ActionButton } from '../components/ActionButton';
import { engineBanner, heroWhen, splitAgenda } from './agendaSplit';
import { EmptyState } from './EmptyState';
import { formatTimeRange } from './timeRange';
import styles from './TodayView.module.css';

export type TodayConnection = CalendarAccess;

interface TodayViewProps {
  connection: TodayConnection;
  agenda: AgendaEvent[];
  upcoming: UpcomingDay[];
  recording: RecordingInfo;
  elapsedText: string;
  /** Wall-clock now in ms; decides which events are earlier, current or next. */
  nowMs: number;
  onStartMeeting: () => void;
  onOpenNotes: () => void;
  onRetryMicrophone: () => void;
  onSelectMeeting: (meetingId: string) => void;
  onRecord: (key: string) => void;
  onConnectCalendar: () => void;
  onOpenPrivacySettings: () => void;
}

function peopleLabel(count: number): string {
  return count === 1 ? '1 person' : `${count} people`;
}

function eventsLabel(count: number): string {
  return count === 1 ? '1 event' : `${count} events`;
}

function timeRange(event: AgendaEvent): string {
  return formatTimeRange(event.time, event.endTime);
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
  recording,
  elapsedText,
  nowMs,
  onStartMeeting,
  onOpenNotes,
  onRetryMicrophone,
  onSelectMeeting,
  onRecord,
  onConnectCalendar,
  onOpenPrivacySettings,
}: TodayViewProps) {
  const recordingActive = recording.recording || recording.processing;
  const split = useMemo(() => splitAgenda(agenda, nowMs, recordingActive), [agenda, nowMs, recordingActive]);
  const banner = engineBanner(recording);
  const [openDays, setOpenDays] = useState<Set<number>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [showEarlier, setShowEarlier] = useState(false);

  const header = (
    <div className={styles.header}>
      <h1 className={styles.title}>Today</h1>
      {!recordingActive && (
        <ActionButton variant="strong" onClick={onStartMeeting}>
          Start Meeting
        </ActionButton>
      )}
    </div>
  );

  function renderRow(event: AgendaEvent) {
    const body = (
      <>
        <span className={styles.time}>{timeRange(event)}</span>
        <span className={styles.eventTitle}>{event.title}</span>
        {event.attendeeCount > 0 && <span className={styles.attendees}>{peopleLabel(event.attendeeCount)}</span>}
        <span className={styles.statusCell}>{statusCell(event, onRecord)}</span>
      </>
    );
    return (
      <div key={event.key} className={styles.rowWrap} role="listitem">
        {event.status === 'recorded' ? (
          <button className={styles.row} onClick={() => event.meetingId && onSelectMeeting(event.meetingId)}>
            {body}
          </button>
        ) : (
          <div className={styles.row}>{body}</div>
        )}
      </div>
    );
  }

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
        {header}
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
        {header}
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

  const moreCount = split.next.length + split.more.length;
  const nothingToday = agenda.length === 0;

  return (
    <div className={styles.view}>
      {header}

      {banner && (
        <div className={styles.banner} role="status">
          <span className={styles.bannerText}>{banner.text}</span>
          {banner.action === 'retry' && <ActionButton onClick={onRetryMicrophone}>Retry Microphone</ActionButton>}
        </div>
      )}

      {recordingActive && (
        <div className={styles.hero}>
          <div className={styles.heroBody}>
            <div className={styles.heroTitle}>
              <span className={styles.recordingDot} aria-hidden="true" /> Recording · {recording.title ?? 'Untitled meeting'} · {elapsedText}
            </div>
          </div>
          <ActionButton onClick={onOpenNotes}>Open notes</ActionButton>
        </div>
      )}
      {!recordingActive && split.hero && (
        <div className={styles.hero}>
          <div className={styles.heroBody}>
            <div className={styles.heroTitle}>{split.hero.title}</div>
            <div className={styles.heroMeta}>
              {heroWhen(split.hero, nowMs)}
              {split.hero.attendeeCount > 0 && ` · ${peopleLabel(split.hero.attendeeCount)}`}
            </div>
          </div>
          <span className={styles.statusCell}>{statusCell(split.hero, onRecord)}</span>
        </div>
      )}
      {!recordingActive && !split.hero && nothingToday && <EmptyState title="Nothing on your calendar today." />}
      {!recordingActive && !split.hero && !nothingToday && <div className={styles.quiet}>Nothing else today</div>}

      {(split.next.length > 0 || (showAll && split.more.length > 0)) && (
        <div className={styles.agenda} role="list" aria-label="Today's agenda">
          {split.next.map(renderRow)}
          {showAll && split.more.map(renderRow)}
        </div>
      )}
      {split.more.length > 0 && (
        <div className={styles.foldRow}>
          <button className={styles.textButton} aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Show fewer' : `Show all ${moreCount} events`}
          </button>
        </div>
      )}

      {split.earlier.length > 0 && (
        <div className={styles.earlier}>
          <button
            className={styles.disclosureRow}
            aria-expanded={showEarlier}
            aria-controls="today-earlier-panel"
            onClick={() => setShowEarlier((v) => !v)}
          >
            <span className={showEarlier ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} aria-hidden="true">
              ›
            </span>
            Earlier today · {split.earlier.length} {split.earlier.length === 1 ? 'meeting' : 'meetings'}, {split.earlierRecorded} recorded
          </button>
          {showEarlier && (
            <div id="today-earlier-panel" className={styles.agenda} role="list" aria-label="Earlier today">
              {split.earlier.map(renderRow)}
            </div>
          )}
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
                        {event.attendeeCount > 0 && <span className={styles.attendees}>{peopleLabel(event.attendeeCount)}</span>}
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
