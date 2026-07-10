import type { ReactNode } from 'react';
import styles from './PrimaryButton.module.css';

interface PrimaryButtonProps {
  children: ReactNode;
  danger?: boolean;
  className?: string;
  onClick?: () => void;
}

export function PrimaryButton({ children, danger = false, className, onClick }: PrimaryButtonProps) {
  const classes = [styles.btn, danger ? styles.danger : '', className].filter(Boolean).join(' ');
  return (
    <button className={classes} onClick={onClick}>
      {children}
    </button>
  );
}
