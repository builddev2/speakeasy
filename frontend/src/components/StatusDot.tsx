import styles from './StatusDot.module.css';

interface StatusDotProps {
  variant: 'green' | 'rec' | 'amber';
}

export function StatusDot({ variant }: StatusDotProps) {
  return <span className={`${styles.dot} ${styles[variant]}`} />;
}
