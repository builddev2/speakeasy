import { useEffect, useState } from 'react';
import type { LibraryStatus } from '../mock/meetings';
import styles from './LibraryBanner.module.css';

interface LibraryBannerProps {
  status: LibraryStatus;
  dismissed?: boolean;
}

function skippedLine(skipped: { file: string; reason: string }): string {
  return skipped.file ? `${skipped.file} — ${skipped.reason}` : skipped.reason;
}

function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

/**
 * Auto-dismiss for the "N meetings upgraded" banner. Lives in the parent (App),
 * NOT inside LibraryBanner: the banner is unmounted whenever the list column is
 * swapped for search results / Today, and a remount used to reset the fade state
 * and restart the 4 s timer, so the banner kept coming back. Keyed on primitives
 * (state, done, skipped count) so a re-sent identical status does not restart it,
 * and the clock only runs while the page is actually visible (WebKit throttles
 * or suspends timers in hidden/occluded views).
 */
export function useLibraryBannerDismissed(status: LibraryStatus): boolean {
  const { state, done } = status;
  const skippedCount = status.skipped?.length ?? 0;
  const [dismissed, setDismissed] = useState(false);
  const [visible, setVisible] = useState(
    typeof document === 'undefined' || document.visibilityState !== 'hidden',
  );

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  // A state change (e.g. upgrading -> done) is a genuinely new banner.
  useEffect(() => {
    setDismissed(false);
  }, [state]);

  useEffect(() => {
    // Skipped files mean there's something the user may still need to read
    // (and the details panel only exists then) — never auto-fade those.
    if (state !== 'done' || skippedCount > 0 || !visible) return;
    const timer = window.setTimeout(() => setDismissed(true), 4000);
    return () => window.clearTimeout(timer);
  }, [state, done, skippedCount, visible]);

  return dismissed;
}

export function LibraryBanner({ status, dismissed = false }: LibraryBannerProps) {
  const [showDetails, setShowDetails] = useState(false);

  if (status.state === 'idle') return null;
  if (status.state === 'done' && dismissed && status.skipped.length === 0) return null;

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
        {skippedCount > 0 && ` · ${pluralize(skippedCount, "file")} couldn't be imported`}
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
