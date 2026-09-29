import { useRef } from 'react';
import { Sheet } from './Sheet';
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

  return (
    <Sheet ariaLabel={title} role="alertdialog" onClose={onCancel} initialFocusRef={cancelRef} className={styles.sheet}>
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
    </Sheet>
  );
}
