import type { ReactNode } from 'react';
import styles from './EmptyState.module.css';

interface EmptyStateProps {
  title: string;
  body?: string;
  action?: ReactNode;
}

/** A calm, single-sentence empty/denied/error state with at most one action. */
export function EmptyState({ title, body, action }: EmptyStateProps) {
  return (
    <div className={styles.wrap}>
      <div className={styles.title}>{title}</div>
      {body && <div className={styles.body}>{body}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}
