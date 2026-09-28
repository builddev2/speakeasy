import { forwardRef } from 'react';
import type { ReactNode } from 'react';
import styles from './PrimaryButton.module.css';

interface PrimaryButtonProps {
  children: ReactNode;
  danger?: boolean;
  className?: string;
  onClick?: () => void;
}

export const PrimaryButton = forwardRef<HTMLButtonElement, PrimaryButtonProps>(function PrimaryButton(
  { children, danger = false, className, onClick },
  ref,
) {
  const classes = [styles.btn, danger ? styles.danger : '', className].filter(Boolean).join(' ');
  return (
    <button ref={ref} className={classes} onClick={onClick}>
      {children}
    </button>
  );
});
