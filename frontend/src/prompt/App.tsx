import { useEffect, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { bridge } from '../bridge';
import styles from './App.module.css';

type Phase = 'prompt' | 'recording' | 'dismissed';

interface PromptAppProps {
  eventTitle?: string;
  attendeeCount?: number;
}

function peopleLabel(count: number): string {
  return count === 1 ? '1 person' : `${count} people`;
}

/**
 * The record banner (mock only): a non-activating notification-style panel
 * offering to start a linked-event recording. Task 10 wires Record/Not Now
 * to the bridge and the real dismiss timeout; here the phases are simulated
 * locally so the states can be reviewed.
 */
export function PromptApp({ eventTitle = 'Weekly 1:1 — Alex', attendeeCount = 3 }: PromptAppProps) {
  const [phase, setPhase] = useState<Phase>('prompt');

  useEffect(() => {
    if (phase !== 'recording') return;
    const timer = window.setTimeout(() => setPhase('dismissed'), 2000);
    return () => window.clearTimeout(timer);
  }, [phase]);

  // Mock-only loop: bring the banner back after a dismissal so every phase
  // can be re-checked in preview without reloading. The real behavior —
  // Not Now (or 5 minutes with no action) dismissing for that event only —
  // lands in phase 4 once this is wired to the bridge.
  useEffect(() => {
    if (phase !== 'dismissed') return;
    const timer = window.setTimeout(() => setPhase('prompt'), 1500);
    return () => window.clearTimeout(timer);
  }, [phase]);

  function onRecord() {
    setPhase('recording');
    if (!bridge.embedded) console.log('record');
  }

  function onNotNow() {
    setPhase('dismissed');
    if (!bridge.embedded) console.log('not-now');
  }

  if (phase === 'dismissed') {
    return <GlassPanel width={360} height={92}><div className={styles.dismissed} /></GlassPanel>;
  }

  return (
    <GlassPanel width={360} height={92}>
      <div
        className={styles.banner}
        role="alertdialog"
        aria-label={phase === 'recording' ? 'Recording' : `Record ${eventTitle}?`}
      >
        <div className={styles.top}>
          <div className={styles.icon} aria-hidden="true">
            🎙️
          </div>
          <div className={styles.text}>
            <div className={styles.title}>{eventTitle}</div>
            <div className={styles.subtitle}>
              {phase === 'recording' ? 'Recording' : `Starting now · ${peopleLabel(attendeeCount)}`}
            </div>
          </div>
        </div>
        {phase === 'prompt' && (
          <div className={styles.actions}>
            <button className={styles.notNow} onClick={onNotNow}>
              Not Now
            </button>
            <button className={styles.record} onClick={onRecord}>
              Record
            </button>
          </div>
        )}
      </div>
    </GlassPanel>
  );
}
