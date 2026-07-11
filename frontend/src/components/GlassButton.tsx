import type { ReactNode } from 'react';
import styles from './GlassButton.module.css';

interface GlassButtonProps {
  icon: ReactNode;
  label: string;
  dim?: boolean;
  onClick?: () => void;
}

export function GlassButton({ icon, label, dim = false, onClick }: GlassButtonProps) {
  return (
    <button className={dim ? `${styles.btn} ${styles.dim}` : styles.btn} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}
