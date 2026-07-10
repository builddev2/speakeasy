import type { ReactNode } from 'react';
import styles from './AppIdentity.module.css';

interface AppIdentityProps {
  status: ReactNode;
  live?: boolean;
}

export function AppIdentity({ status, live = false }: AppIdentityProps) {
  return (
    <div className={styles.identity}>
      <div className={styles.icon}>💀</div>
      <div className={styles.textBlock}>
        <div className={styles.wordmark}>Speakeasy</div>
        <div className={live ? `${styles.status} ${styles.statusLive}` : styles.status}>{status}</div>
      </div>
    </div>
  );
}
