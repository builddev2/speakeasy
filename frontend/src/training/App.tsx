import { useState } from 'react';
import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import styles from './App.module.css';

interface TrainingAppProps {
  operatorName?: string;
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#E8955A" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

const CHECKED_SESSIONS = ['Everyday phrases', 'Tech & jargon', 'Names & proper nouns'];

export function TrainingApp({ operatorName = 'Jason' }: TrainingAppProps) {
  const [practiceText, setPracticeText] = useState('');

  return (
    <GlassPanel width={640} height={440}>
      <TitleBar title={<>Training — <strong>{operatorName}</strong></>} />
      <div className={styles.split}>
        <div className={styles.sidebar}>
          <span className={styles.heading}>Sessions</span>
          {CHECKED_SESSIONS.map((name) => (
            <div className={styles.item} key={name}>
              <CheckIcon />
              <span className={styles.name}>{name}</span>
            </div>
          ))}
          <div className={`${styles.item} ${styles.itemCurrent}`}>
            <span className={styles.bullet} />
            <span className={styles.name}>Numbers &amp; units</span>
            <span className={styles.star}>★</span>
          </div>
          <div className={styles.item}>
            <span className={styles.bullet} />
            <span className={`${styles.name} ${styles.nameTrunc}`}>Tricky words &amp; homophones</span>
          </div>
        </div>
        <div className={styles.detail}>
          <span className={styles.intro}>Short read-aloud sessions teach Speakeasy your words.</span>
          <h2 className={styles.headline}>Pick a session on the left, or practice your own words below.</h2>
          <div className={styles.inputRow}>
            <input
              className={styles.textInput}
              type="text"
              placeholder="Your own word or phrase…"
              value={practiceText}
              onChange={(event) => setPracticeText(event.target.value)}
            />
            <button className={styles.practiceBtn} onClick={() => console.log('practice', practiceText)}>
              Practice
            </button>
          </div>
        </div>
      </div>
    </GlassPanel>
  );
}
