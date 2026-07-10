import { useEffect, useRef, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { AppIdentity } from '../components/AppIdentity';
import { StatusDot } from '../components/StatusDot';
import { PrimaryButton } from '../components/PrimaryButton';
import { GlassButton } from '../components/GlassButton';
import { Waveform } from '../components/Waveform';
import { bridge } from '../bridge';
import styles from './App.module.css';

type EngineMode =
  | 'loading' | 'ready' | 'recording' | 'transcribing' | 'paused'
  | 'meeting_recording' | 'meeting_processing';

interface AppState {
  mode: EngineMode;
  profileName: string;
  elapsedSeconds: number;
  progressText: string | null;
}

interface DockAppProps {
  operatorName?: string;
  readyMessage?: string;
}

const STATUS: Record<Exclude<EngineMode, 'meeting_recording'>, { text: string; dot: 'green' | 'rec' | 'amber' }> = {
  loading: { text: 'Loading model…', dot: 'amber' },
  ready: { text: 'Ready', dot: 'green' },
  recording: { text: 'Listening…', dot: 'rec' },
  transcribing: { text: 'Transcribing…', dot: 'amber' },
  paused: { text: 'Paused', dot: 'amber' },
  meeting_processing: { text: 'Processing meeting…', dot: 'amber' },
};

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
  const [app, setApp] = useState<AppState>({
    mode: 'ready',
    profileName: operatorName,
    elapsedSeconds: 0,
    progressText: null,
  });
  const [tick, setTick] = useState(0);
  const baselineRef = useRef<number | null>(null);

  function applyState(next: AppState) {
    if (next.mode === 'meeting_recording') {
      baselineRef.current = Date.now() - next.elapsedSeconds * 1000;
    } else {
      baselineRef.current = null;
    }
    setApp(next);
  }

  useEffect(() => {
    if (!bridge.embedded) return;
    bridge.call<AppState>('app.getState').then(applyState).catch(() => {});
    return bridge.on('state', (payload) => applyState(payload as AppState));
  }, []);

  // Browser preview only: R toggles a fake meeting.
  useEffect(() => {
    if (bridge.embedded) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== 'r' && event.key !== 'R') return;
      setApp((current) => {
        const recording = current.mode === 'meeting_recording';
        baselineRef.current = recording ? null : Date.now();
        return { ...current, mode: recording ? 'ready' : 'meeting_recording', elapsedSeconds: 0 };
      });
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const isRecording = app.mode === 'meeting_recording';

  useEffect(() => {
    if (!isRecording) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [isRecording]);
  void tick;

  const elapsed = baselineRef.current === null
    ? 0
    : Math.max(0, Math.floor((Date.now() - baselineRef.current) / 1000));

  function onPrimary() {
    if (bridge.embedded) {
      void bridge.call(isRecording ? 'app.endMeeting' : 'app.beginMeeting');
    } else {
      setApp((current) => {
        const recording = current.mode === 'meeting_recording';
        baselineRef.current = recording ? null : Date.now();
        return { ...current, mode: recording ? 'ready' : 'meeting_recording', elapsedSeconds: 0 };
      });
    }
  }

  function openWindow(name: 'meetings' | 'training') {
    if (bridge.embedded) void bridge.call('app.openWindow', { name });
    else console.log('open', name);
  }

  function onQuit(event: React.MouseEvent) {
    event.preventDefault();
    if (bridge.embedded) void bridge.call('app.quit');
    else console.log('quit');
  }

  const idleInfo = isRecording ? null : STATUS[app.mode as Exclude<EngineMode, 'meeting_recording'>];
  const name = bridge.embedded ? app.profileName : operatorName;
  const primaryDisabled = !isRecording && app.mode !== 'ready';

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
                <span className={styles.timer}>· {formatTimer(elapsed)}</span>
              </>
            ) : (
              <>
                <StatusDot variant={idleInfo!.dot} />{' '}
                {app.mode === 'ready' ? (
                  <>
                    {bridge.embedded ? 'Ready' : readyMessage} —{' '}
                    <span className={styles.name}>{name}</span>
                  </>
                ) : app.mode === 'meeting_processing' ? (
                  <span className={styles.progress}>{app.progressText ?? idleInfo!.text}</span>
                ) : (
                  idleInfo!.text
                )}
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
          onClick={primaryDisabled ? undefined : onPrimary}
        >
          {isRecording ? 'End Meeting' : 'Begin Meeting'}
        </PrimaryButton>

        <div className={isRecording ? `${styles.row} ${styles.rowDisabled}` : styles.row}>
          <GlassButton icon={<MeetingsIcon />} label="Meetings" dim={isRecording} onClick={() => openWindow('meetings')} />
          <GlassButton icon={<TrainIcon />} label="Train Profile" dim={isRecording} onClick={() => openWindow('training')} />
        </div>

        <div className={styles.foot}>
          <a className={styles.quit} href="#" onClick={onQuit}>Quit Speakeasy</a>
        </div>
      </div>
    </GlassPanel>
  );
}
