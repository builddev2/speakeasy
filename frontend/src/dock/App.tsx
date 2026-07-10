import { useEffect, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { AppIdentity } from '../components/AppIdentity';
import { StatusDot } from '../components/StatusDot';
import { PrimaryButton } from '../components/PrimaryButton';
import { GlassButton } from '../components/GlassButton';
import { Waveform } from '../components/Waveform';
import styles from './App.module.css';

interface DockAppProps {
  operatorName?: string;
  readyMessage?: string;
}

function formatTimer(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, '0');
  const seconds = (totalSeconds % 60).toString().padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function MeetingsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.6)" strokeWidth="1.7" strokeLinecap="round">
      <rect x="4" y="3.5" width="16" height="17" rx="2.5" />
      <line x1="8" y1="8" x2="16" y2="8" />
      <line x1="8" y1="12" x2="16" y2="12" />
      <line x1="8" y1="16" x2="13" y2="16" />
    </svg>
  );
}

function TrainIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.6)" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4" />
      <path d="M4.5 20c0-3.6 3.4-6 7.5-6s7.5 2.4 7.5 6" />
    </svg>
  );
}

export function DockApp({ operatorName = 'Jason', readyMessage = 'Ready' }: DockAppProps) {
  const [mode, setMode] = useState<'idle' | 'recording'>('idle');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  function toggleMode() {
    setMode((current) => (current === 'idle' ? 'recording' : 'idle'));
    setElapsedSeconds(0);
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'r' || event.key === 'R') {
        toggleMode();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    if (mode !== 'recording') return;
    const id = window.setInterval(() => {
      setElapsedSeconds((current) => current + 1);
    }, 1000);
    return () => window.clearInterval(id);
  }, [mode]);

  const isRecording = mode === 'recording';

  return (
    <GlassPanel width={360} height={300}>
      <TitleBar plain />
      <div className={styles.body}>
        <AppIdentity
          live={isRecording}
          status={
            isRecording ? (
              <>
                <StatusDot variant="rec" /> Recording meeting{' '}
                <span className={styles.timer}>· {formatTimer(elapsedSeconds)}</span>
              </>
            ) : (
              <>
                <StatusDot variant="green" /> {readyMessage} —{' '}
                <span className={styles.name}>{operatorName}</span>
              </>
            )
          }
        />

        {isRecording && <Waveform />}
        {isRecording && (
          <div className={styles.recStatus}>
            <span className={styles.pillBadge}>Dictation paused</span>
          </div>
        )}

        <PrimaryButton
          danger={isRecording}
          className={isRecording ? styles.primaryRecording : styles.primaryIdle}
          onClick={toggleMode}
        >
          {isRecording ? 'End Meeting' : 'Begin Meeting'}
        </PrimaryButton>

        <div className={isRecording ? `${styles.row} ${styles.rowDisabled}` : styles.row}>
          <GlassButton icon={<MeetingsIcon />} label="Meetings" dim={isRecording} />
          <GlassButton icon={<TrainIcon />} label="Train Profile" dim={isRecording} />
        </div>

        <div className={styles.foot}>
          <a className={styles.quit} href="#">Quit Speakeasy</a>
        </div>
      </div>
    </GlassPanel>
  );
}
