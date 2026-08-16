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
  canTrain: boolean;
  voiceProfiles: string[];
  captureMode: 'mic_only' | 'mic_and_system';
  captureScope: 'global' | 'selected' | 'mic_only' | 'selected_app_unavailable';
  systemAudioStatus: string;
  captureHealth: {
    system_first_buffer?: boolean;
    system_nonzero_signal?: boolean;
    mic_writer_failed?: boolean;
    system_writer_failed?: boolean;
    mic_writer_lagged?: boolean;
    system_writer_lagged?: boolean;
    helper_exited?: boolean;
    helper_exit_reason?: string | null;
  };
}

interface DockAppProps {
  operatorName?: string;
  readyMessage?: string;
}

interface CaptureApplication {
  pid: number;
  name: string;
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

function captureStatusText(status: string): string {
  if (status === 'requires_macos_14_2') return 'Microphone only — system audio needs macOS 14.2+';
  if (status === 'helper_missing') return 'Microphone only — system audio helper unavailable';
  if (status === 'permission_denied_or_unavailable') return 'Microphone only — system audio permission denied';
  if (status === 'selected_app_unavailable') return 'Microphone only — selected application unavailable';
  return 'Microphone only — remote voices need speakers';
}

function liveCaptureStatus(app: AppState): string {
  const health = app.captureHealth;
  if (health.helper_exited && health.helper_exit_reason !== 'requested_stop') {
    return 'Microphone only — system audio helper exited';
  }
  if (health.system_writer_failed) return 'Microphone only — system audio writer failed';
  if (health.mic_writer_failed) return 'Microphone writer failed';
  if (app.captureMode !== 'mic_and_system') return captureStatusText(app.systemAudioStatus);
  const source = app.captureScope === 'selected' ? 'selected application' : 'system audio';
  if (!health.system_first_buffer) return `Microphone + ${source} — waiting for data`;
  if (!health.system_nonzero_signal) return `Microphone + ${source} — no signal yet`;
  if (health.mic_writer_lagged || health.system_writer_lagged) {
    return `Microphone + ${source} — capture lag detected`;
  }
  return `Microphone + ${source}`;
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
    canTrain: true,
    voiceProfiles: [],
    captureMode: 'mic_only',
    captureScope: 'mic_only',
    systemAudioStatus: 'available',
    captureHealth: {},
  });
  const [expectedSpeakerCount, setExpectedSpeakerCount] = useState('1');
  const [useVoiceProfiles, setUseVoiceProfiles] = useState(false);
  const [captureApps, setCaptureApps] = useState<CaptureApplication[]>([]);
  const [captureSelection, setCaptureSelection] = useState('global');
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

  function refreshCaptureApps() {
    if (!bridge.embedded) return;
    bridge.call<CaptureApplication[]>('app.listCaptureApplications')
      .then(setCaptureApps)
      .catch(() => setCaptureApps([]));
  }

  useEffect(() => {
    if (!bridge.embedded) return;
    bridge.call<AppState>('app.getState').then(applyState).catch(() => {});
    refreshCaptureApps();
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
      void bridge.call(isRecording ? 'app.endMeeting' : 'app.beginMeeting', isRecording ? {} : {
        expectedSpeakerCount: expectedSpeakerCount === '' ? null : Number(expectedSpeakerCount),
        expectedVoiceProfileNames: useVoiceProfiles ? app.voiceProfiles : [],
        systemAudioPid: captureSelection === 'global' ? null : Number(captureSelection),
      });
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
  const selectedAppAvailable = captureSelection === 'global'
    || captureApps.some((application) => String(application.pid) === captureSelection);
  const primaryDisabled = !isRecording && (app.mode !== 'ready' || !selectedAppAvailable);

  return (
    <GlassPanel width={360} height={335}>
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
            <span className={app.captureMode === 'mic_and_system' ? styles.captureGood : styles.captureWarning}>
              {liveCaptureStatus(app)}
            </span>
          </div>
        )}

        {!isRecording && app.mode === 'ready' && (
          <div className={styles.meetingOptions}>
            <label className={styles.captureOption}>
              System audio
              <select
                value={captureSelection}
                title="Browser choices capture that browser process, not an individual tab."
                onFocus={refreshCaptureApps}
                onChange={(event) => setCaptureSelection(event.target.value)}
              >
                <option value="global">All system audio</option>
                {!selectedAppAvailable && (
                  <option value={captureSelection}>Selected application unavailable — reselect</option>
                )}
                {captureApps.map((application) => (
                  <option key={application.pid} value={application.pid}>
                    {application.name} — PID {application.pid}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Remote speakers (excluding you)
              <input
                type="number"
                min="1"
                max="20"
                placeholder="Auto"
                value={expectedSpeakerCount}
                onChange={(event) => setExpectedSpeakerCount(event.target.value)}
              />
            </label>
            {app.voiceProfiles.length > 0 && (
              <label className={styles.voiceOption}>
                <input
                  type="checkbox"
                  checked={useVoiceProfiles}
                  onChange={(event) => setUseVoiceProfiles(event.target.checked)}
                />
                Identify enrolled voices
              </label>
            )}
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
          <GlassButton
            icon={<TrainIcon />}
            label="Train Profile"
            dim={isRecording || !app.canTrain}
            title={app.canTrain ? undefined : 'Pick a profile (not Guest) and wait for the model to load.'}
            onClick={app.canTrain ? () => openWindow('training') : undefined}
          />
        </div>

        <div className={styles.foot}>
          <a className={styles.quit} href="#" onClick={onQuit}>Quit Speakeasy</a>
        </div>
      </div>
    </GlassPanel>
  );
}
