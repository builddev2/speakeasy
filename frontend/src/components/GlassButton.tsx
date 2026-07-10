import type { ReactNode } from 'react';
import styles from './GlassButton.module.css';

interface GlassButtonProps {
  icon: ReactNode;
  label: string;
  dim?: boolean;
}

export function GlassButton({ icon, label, dim = false }: GlassButtonProps) {
  return (
    <button className={dim ? `${styles.btn} ${styles.dim}` : styles.btn}>
      {icon}
      <span>{label}</span>
    </button>
  );
}
