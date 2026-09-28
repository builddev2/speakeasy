import { useEffect, useRef, useState } from 'react';
import styles from './SpeakerPopover.module.css';

interface SpeakerPopoverProps {
  label: string;
  anchor: { top: number; left: number };
  onCancel: () => void;
  onRename: (name: string, allMatching: boolean) => void;
}

export function SpeakerPopover({ label, anchor, onCancel, onRename }: SpeakerPopoverProps) {
  const [name, setName] = useState(label);
  const [allMatching, setAllMatching] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  function commit() {
    const trimmed = name.trim();
    if (trimmed === '') return;
    onRename(trimmed, allMatching);
  }

  return (
    <div className={styles.overlay} onClick={onCancel}>
      <div
        className={styles.popover}
        style={{ top: anchor.top, left: anchor.left }}
        role="dialog"
        aria-label="Rename speaker"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') onCancel();
        }}
      >
        <input
          ref={inputRef}
          className={styles.input}
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Speaker name"
        />
        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={allMatching}
            onChange={(e) => setAllMatching(e.target.checked)}
          />
          <span>Rename all &lsquo;{label}&rsquo; in this meeting</span>
        </label>
        <div className={styles.actions}>
          <button className={styles.cancel} onClick={onCancel} autoFocus>
            Cancel
          </button>
          <button className={styles.rename} onClick={commit}>
            Rename
          </button>
        </div>
      </div>
    </div>
  );
}
