import { useEffect, useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { bridge } from '../bridge';
import styles from './App.module.css';

interface TrainingAppProps {
  operatorName?: string;
}

interface SessionInfo {
  name: string;
  done: boolean;
  suggested: boolean;
}

interface PracticeState {
  status: string;
  phrase: string | null;
  feedback: string[];
}

const MOCK_SESSIONS: SessionInfo[] = [
  { name: 'Everyday phrases', done: true, suggested: false },
  { name: 'Tech & jargon', done: true, suggested: false },
  { name: 'Names & proper nouns', done: true, suggested: false },
  { name: 'Numbers & units', done: false, suggested: true },
  { name: 'Tricky words & homophones', done: false, suggested: false },
];

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#E8955A" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

export function TrainingApp({ operatorName = 'Jason' }: TrainingAppProps) {
  const [sessions, setSessions] = useState<SessionInfo[]>(bridge.embedded ? [] : MOCK_SESSIONS);
  const [practiceText, setPracticeText] = useState('');
  const [practice, setPractice] = useState<PracticeState | null>(null);
  const [profileName, setProfileName] = useState(operatorName);

  function refreshSessions() {
    void bridge.call<{ sessions: SessionInfo[]; profileName: string }>('training.listSessions')
      .then(({ sessions: s, profileName: p }) => {
        setSessions(s);
        setProfileName(p);
      })
      .catch(() => {});
  }

  useEffect(() => {
    if (!bridge.embedded) return;
    refreshSessions();
    const offPrompt = bridge.on('training.prompt', (payload) => {
      const { text, status } = payload as { text: string; status: string };
      setPractice((current) => ({
        status,
        phrase: text,
        feedback: current?.feedback ?? [],
      }));
    });
    const offFeedback = bridge.on('training.feedback', (payload) => {
      const { line } = payload as { line: string };
      setPractice((current) =>
        current === null
          ? null
          : { ...current, feedback: [...current.feedback.slice(-3), line] },
      );
    });
    const offDone = bridge.on('training.done', (payload) => {
      const { message } = payload as { message: string };
      setPractice((current) =>
        current === null
          ? { status: message, phrase: null, feedback: [] }
          : { ...current, status: message, phrase: null },
      );
      refreshSessions();
    });
    const offSessions = bridge.on('training.sessions', (payload) => {
      const { sessions: s, profileName: p } = payload as { sessions: SessionInfo[]; profileName: string };
      setSessions(s);
      setProfileName(p);
    });
    return () => {
      offPrompt();
      offFeedback();
      offDone();
      offSessions();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function startSession(name: string) {
    if (bridge.embedded) {
      setPractice({ status: 'Starting session…', phrase: null, feedback: [] });
      void bridge.call('training.startSession', { name }).catch(() => setPractice(null));
    } else {
      console.log('start session', name);
    }
  }

  function startPractice() {
    const term = practiceText.trim();
    if (term === '') return;
    setPracticeText('');
    if (bridge.embedded) {
      setPractice({ status: 'Starting…', phrase: null, feedback: [] });
      void bridge.call('training.practice', { text: term }).catch(() => setPractice(null));
    } else {
      console.log('practice', term);
    }
  }

  return (
    <GlassPanel width={640} height={440}>
      <TitleBar title={<>Training — <strong>{profileName}</strong></>} />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Sessions</span>
          {sessions.map((s) => (
            <button
              key={s.name}
              className={
                s.suggested
                  ? `${styles.item} ${styles.itemCurrent} ${styles.itemClickable}`
                  : `${styles.item} ${styles.itemClickable}`
              }
              onClick={() => startSession(s.name)}
            >
              {s.done ? <CheckIcon /> : <span className={styles.bullet} />}
              <span className={s.done ? styles.name : `${styles.name} ${styles.nameTrunc}`}>{s.name}</span>
              {s.suggested && <span className={styles.star}>★</span>}
            </button>
          ))}
        </div>
        <div className={styles.detail}>
          {practice === null ? (
            <>
              <span className={styles.intro}>Short read-aloud sessions teach Speakeasy your words.</span>
              <h2 className={styles.headline}>Pick a session on the left, or practice your own words below.</h2>
            </>
          ) : (
            <>
              <span className={styles.intro}>{practice.status}</span>
              {practice.phrase !== null && <h2 className={styles.phrase}>“{practice.phrase}”</h2>}
              {practice.feedback.length > 0 && (
                <div className={styles.feedback}>
                  {practice.feedback.map((line, i) => (
                    <span key={i}>{line}</span>
                  ))}
                </div>
              )}
            </>
          )}
          <div className={styles.inputRow}>
            <input
              className={styles.textInput}
              type="text"
              placeholder="Your own word or phrase…"
              value={practiceText}
              onChange={(event) => setPracticeText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') startPractice();
              }}
            />
            <button className={styles.practiceBtn} onClick={startPractice}>
              Practice
            </button>
          </div>
        </div>
      </div>
    </GlassPanel>
  );
}
