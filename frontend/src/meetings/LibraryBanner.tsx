import { useEffect, useState } from 'react';
import type { LibraryStatus } from '../mock/meetings';
import styles from './LibraryBanner.module.css';

interface LibraryBannerProps {
  status: LibraryStatus;
}

function skippedLine(skipped: { file: string; reason: string }): string {
  return skipped.file ? `${skipped.file} — ${skipped.reason}` : skipped.reason;
}

export function LibraryBanner({ status }: LibraryBannerProps) {
  const [showDetails, setShowDetails] = useState(false);
  const [faded, setFaded] = useState(false);

  useEffect(() => {
    setFaded(false);
    if (status.state !== 'done') return;
    const timer = window.setTimeout(() => setFaded(true), 4000);
    return () => window.clearTimeout(timer);
  }, [status]);

  if (status.state === 'idle') return null;
  if (status.state === 'done' && faded) return null;

  if (status.state === 'upgrading') {
    const pct = status.total > 0 ? Math.round((status.done / status.total) * 100) : 0;
    return (
      <div className={styles.banner} role="status">
        <div className={styles.line}>
          Upgrading your meeting library… {status.done} of {status.total}
        </div>
        <div className={styles.track}>
          <div className={styles.fill} style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  if (status.state === 'failed') {
    return (
      <div className={styles.banner} role="status">
        <div className={styles.line}>Your meetings are safe and unchanged. The upgrade will try again next launch.</div>
        {status.skipped.length > 0 && (
          <button className={styles.details} onClick={() => setShowDetails((v) => !v)}>
            {showDetails ? 'Hide Details' : 'Show Details'}
          </button>
        )}
        {showDetails && (
          <ul className={styles.detailsList}>
            {status.skipped.map((s, i) => (
              <li key={i}>{skippedLine(s)}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  // done
  const skippedCount = status.skipped.length;
  return (
    <div className={styles.banner} role="status">
      <div className={styles.line}>
        {status.done} meetings upgraded
        {skippedCount > 0 && ` · ${skippedCount} files couldn't be imported`}
        {skippedCount > 0 && (
          <>
            {' · '}
            <button className={styles.detailsInline} onClick={() => setShowDetails((v) => !v)}>
              {showDetails ? 'Hide Details' : 'Show Details'}
            </button>
          </>
        )}
      </div>
      {showDetails && skippedCount > 0 && (
        <ul className={styles.detailsList}>
          {status.skipped.map((s, i) => (
            <li key={i}>{skippedLine(s)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
