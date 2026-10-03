import { useEffect, useRef } from 'react';
import type { ReactNode, RefObject } from 'react';
import { useFocusTrap } from './useFocusTrap';
import styles from './Sheet.module.css';

interface SheetProps {
  ariaLabel: string;
  onClose: () => void;
  /** Focused on open — the sheet's default action (Cancel, Done, ...). */
  initialFocusRef: RefObject<HTMLElement | null>;
  role?: 'dialog' | 'alertdialog';
  /** Extra class for the sheet's own width/padding; base chrome lives here. */
  className?: string;
  children: ReactNode;
}

/**
 * Shared top-anchored sheet shell: scrim, near-opaque elevated surface, Tab
 * trap, Esc-to-close and initial focus. Used by ConfirmSheet,
 * ConnectClaudeSheet and SettingsSheet so that behavior (and its upkeep)
 * lives in one place.
 */
export function Sheet({ ariaLabel, onClose, initialFocusRef, role = 'dialog', className, children }: SheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const onKeyDown = useFocusTrap(sheetRef, onClose);

  useEffect(() => {
    initialFocusRef.current?.focus({ preventScroll: true });
    if (sheetRef.current) sheetRef.current.scrollTop = 0;
    // Only run on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={styles.overlay} onKeyDown={onKeyDown}>
      <div
        ref={sheetRef}
        className={className ? `${styles.sheet} ${className}` : styles.sheet}
        role={role}
        aria-modal="true"
        aria-label={ariaLabel}
      >
        {children}
      </div>
    </div>
  );
}
