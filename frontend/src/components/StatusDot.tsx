import styles from './StatusDot.module.css';

interface StatusDotProps {
  variant: 'green' | 'rec';
}

export function StatusDot({ variant }: StatusDotProps) {
  return <span className={`${styles.dot} ${styles[variant]}`} />;
}
