import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';
import styles from './ConfirmSheet.module.css';

interface ConfirmSheetProps {
  title: string;
  body: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>('input, button, [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => !el.hasAttribute('disabled'));
}

/** In-page confirmation sheet, replacing window.confirm. Cancel is the default/focused action. */
export function ConfirmSheet({ title, body, confirmLabel, onCancel, onConfirm }: ConfirmSheetProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      onCancel();
      return;
    }
    if (e.key === 'Tab' && sheetRef.current) {
      const focusable = focusableElements(sheetRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  return (
    <div className={styles.overlay} onKeyDown={onKeyDown}>
      <div ref={sheetRef} className={styles.sheet} role="alertdialog" aria-modal="true" aria-label={title}>
        <div className={styles.title}>{title}</div>
        <div className={styles.body}>{body}</div>
        <div className={styles.actions}>
          <button ref={cancelRef} className={styles.cancel} onClick={onCancel}>
            Cancel
          </button>
          <button className={styles.confirm} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
