import { useEffect, useRef } from 'react';
import styles from './ConfirmSheet.module.css';

interface ConfirmSheetProps {
  title: string;
  body: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}

/** In-page confirmation sheet, replacing window.confirm. Cancel is the default/focused action. */
export function ConfirmSheet({ title, body, confirmLabel, onCancel, onConfirm }: ConfirmSheetProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  return (
    <div
      className={styles.overlay}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onCancel();
      }}
    >
      <div className={styles.sheet} role="alertdialog" aria-modal="true" aria-label={title}>
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
