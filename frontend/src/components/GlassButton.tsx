import type { ReactNode } from 'react';
import styles from './GlassButton.module.css';

interface GlassButtonProps {
  icon: ReactNode;
  label: string;
  dim?: boolean;
  title?: string;
  onClick?: () => void;
}

export function GlassButton({ icon, label, dim = false, title, onClick }: GlassButtonProps) {
  return (
    <button className={dim ? `${styles.btn} ${styles.dim}` : styles.btn} title={title} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}
