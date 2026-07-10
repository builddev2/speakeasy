import type { ReactNode } from 'react';
import styles from './TitleBar.module.css';

interface TitleBarProps {
  plain?: boolean;
  title?: ReactNode;
}

export function TitleBar({ plain = false, title }: TitleBarProps) {
  return (
    <div className={plain ? `${styles.titlebar} ${styles.plain}` : styles.titlebar}>
      <span className={`${styles.light} ${styles.red}`} />
      <span className={`${styles.light} ${styles.yellow}`} />
      <span className={`${styles.light} ${styles.green}`} />
      {title && <span className={styles.title}>{title}</span>}
    </div>
  );
}
