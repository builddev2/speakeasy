import type { ReactNode } from 'react';
import styles from './ActionButton.module.css';

interface ActionButtonProps {
  children: ReactNode;
  variant?: 'default' | 'strong' | 'danger';
  className?: string;
  onClick?: () => void;
  disabled?: boolean;
}

export function ActionButton({ children, variant = 'default', className, onClick, disabled }: ActionButtonProps) {
  const variantClass = variant !== 'default' ? styles[variant] : '';
  const classes = [styles.btn, variantClass, className].filter(Boolean).join(' ');
  return (
    <button className={classes} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}
