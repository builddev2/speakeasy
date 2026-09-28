import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import styles from './SpeakerPopover.module.css';

interface SpeakerPopoverProps {
  label: string;
  anchor: { top: number; left: number };
  onCancel: () => void;
  onRename: (name: string, allMatching: boolean) => void;
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>('input, button, [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => !el.hasAttribute('disabled'));
}

export function SpeakerPopover({ label, anchor, onCancel, onRename }: SpeakerPopoverProps) {
  const [name, setName] = useState(label);
  const [allMatching, setAllMatching] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  function commit() {
    const trimmed = name.trim();
    if (trimmed === '') return;
    onRename(trimmed, allMatching);
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      onCancel();
      return;
    }
    if (e.key === 'Enter') {
      // A focused button (Cancel/Rename) handles its own Enter via onClick;
      // only the text field and checkbox should trigger commit here.
      if (document.activeElement === cancelRef.current) return;
      if (document.activeElement instanceof HTMLButtonElement) return;
      commit();
      return;
    }
    if (e.key === 'Tab' && popoverRef.current) {
      const focusable = focusableElements(popoverRef.current);
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
    <div className={styles.overlay} onClick={onCancel}>
      <div
        ref={popoverRef}
        className={styles.popover}
        style={{ top: anchor.top, left: anchor.left }}
        role="dialog"
        aria-label="Rename speaker"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
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
          <button ref={cancelRef} className={styles.cancel} onClick={onCancel}>
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
