import { useEffect, useState } from 'react';
import { bridge } from '../bridge';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import styles from './App.module.css';

type CheckState = {
  phase: string; index?: number; total: number;
  prompt?: { reference: string; condition: string; expected_duration_group: string };
  summary?: { numerical_gate_pass: boolean; weighted_wer: { batch: number | null }; takes: number };
  reportPath?: string;
};

export function DiagnosticApp() {
  const [state, setState] = useState<CheckState>({ phase: 'consent', total: 30 });
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!bridge.embedded) return;
    void bridge.call<CheckState>('diagnostic.state').then(setState);
    return bridge.on('diagnostic.state', payload => setState(payload as CheckState));
  }, []);
  useEffect(() => {
    setSeconds(0);
    if (state.phase !== 'recording') return;
    const timer = window.setInterval(() => setSeconds(s => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [state.phase]);
  function call(method: string) {
    setError('');
    void bridge.call(method).catch(e => setError(e.message));
  }
  const done = ['complete', 'cancelled', 'error'].includes(state.phase);
  const busy = ['processing', 'cancelling'].includes(state.phase);
  const wer = state.summary?.weighted_wer.batch;
  return <GlassPanel width={660} height={600}>
    <TitleBar title="Microphone Check" />
    <main className={styles.body}>
      <div className={styles.eyebrow}>LOCAL · PRIVATE · 30 PROMPTS</div>
      <h1>{state.phase === 'consent' ? 'Check your dictation accuracy' : done ? 'Microphone check results' : `Prompt ${state.index ?? 1} of ${state.total}`}</h1>
      {state.phase === 'consent' ? <>
        <p>Read 30 short, medium and longer prompts. Allow about 12–18 minutes. Start in a quiet room; halfway through, switch to your usual moderate background noise.</p>
        <div className={styles.notice}>
          <strong>You control the microphone.</strong>
          <p>Each prompt has a Start recording and Stop & compare button. Nothing is pasted or used to train your profile.</p>
          <p>Audio stays on this Mac and is deleted after each comparison. Temporary audio expires after 15 minutes while the app runs; crash leftovers are deleted next launch. A private report containing your reference and recognized text is saved until you delete it.</p>
        </div>
        <button className={styles.primary} onClick={() => call('diagnostic.start')}>Begin 30-prompt check</button>
        <p className={styles.muted}>Beginning confirms your consent to these recordings. Recording starts only when you press Start recording.</p>
      </> : done ? <>
        <p>{state.phase === 'complete' ? 'All 30 prompts are finished.' : state.phase === 'cancelled' ? 'Check cancelled. Completed results were saved.' : 'The check stopped because a recording or comparison failed.'}</p>
        {wer != null && <div className={styles.score}>{Math.max(0, (1 - wer) * 100).toFixed(1)}% <span>batch word accuracy</span></div>}
        <p>{state.summary?.numerical_gate_pass ? 'The numerical checks passed. Review the report for truncation or repetition before accepting the results.' : 'This session has not passed all accuracy and coverage checks. The report shows which checks need attention.'}</p>
        <p className={styles.muted}>This shorter check uses 70% fewer recordings. It does not provide the coverage of the former 100-prompt protocol. Normal dictation continues using batch results.</p>
        <button className={styles.secondary} onClick={() => setState({ phase: 'consent', total: 30 })}>New check</button>
        {state.reportPath && <button className={styles.primary} onClick={() => call('diagnostic.report')}>Show saved report</button>}
      </> : <>
        <div className={styles.condition}>{state.prompt?.condition === 'moderate_noise' ? 'Use your usual moderate room noise' : 'Quiet room'} · {state.prompt?.expected_duration_group ?? 'Preparing'} prompt</div>
        <p className={styles.muted}>Read naturally. {state.prompt?.expected_duration_group === 'long' ? 'Allow at least 15 seconds.' : state.prompt?.expected_duration_group === 'medium' ? 'Aim for 5–15 seconds.' : 'Aim for under 5 seconds.'}</p>
        <div className={styles.prompt}>{state.prompt?.reference ?? 'Preparing your microphone…'}</div>
        <div role="status" aria-live="polite" className={styles.status}>{state.phase === 'recording' ? `● Recording · ${seconds}s` : state.phase === 'processing' ? 'Comparing your recording…' : state.phase === 'cancelling' ? 'Cancelling and deleting temporary audio…' : 'Ready when you are'}</div>
        <button className={styles.primary} disabled={busy} onClick={() => call('diagnostic.next')}>{state.phase === 'recording' ? 'Stop & compare' : busy ? 'Please wait…' : 'Start recording'}</button>
        <button className={styles.secondary} disabled={state.phase === 'cancelling'} onClick={() => call('diagnostic.cancel')}>Cancel check</button>
      </>}
      {error && <p role="alert">{error}</p>}
    </main>
  </GlassPanel>;
}
