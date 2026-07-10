import type { ReactNode } from 'react';
import styles from './ActionButton.module.css';

interface ActionButtonProps {
  children: ReactNode;
  variant?: 'default' | 'strong' | 'danger';
  className?: string;
  onClick?: () => void;
}

export function ActionButton({ children, variant = 'default', className, onClick }: ActionButtonProps) {
  const variantClass = variant !== 'default' ? styles[variant] : '';
  const classes = [styles.btn, variantClass, className].filter(Boolean).join(' ');
  return (
    <button className={classes} onClick={onClick}>
      {children}
    </button>
  );
}
