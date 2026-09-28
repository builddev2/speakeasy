import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import styles from './SettingsSheet.module.css';

interface Calendar {
  name: string;
  enabled: boolean;
}

interface CalendarAccount {
  name: string;
  calendars: Calendar[];
}

interface SettingsSheetProps {
  onClose: () => void;
  onExportAll: () => void;
}

// Invented calendar accounts for the mock (holiday/birthday calendars off by
// default, per the spec).
const INITIAL_ACCOUNTS: CalendarAccount[] = [
  {
    name: 'iCloud',
    calendars: [
      { name: 'Personal', enabled: true },
      { name: 'Family', enabled: true },
      { name: 'Holidays', enabled: false },
    ],
  },
  {
    name: 'Work',
    calendars: [
      { name: 'Work', enabled: true },
      { name: 'Team Events', enabled: true },
      { name: 'Birthdays', enabled: false },
    ],
  },
];

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>('input, button, [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => !el.hasAttribute('disabled'));
}

/** Meetings section of Settings: recording offer toggle, calendar checklist, export. */
export function SettingsSheet({ onClose, onExportAll }: SettingsSheetProps) {
  const [offerToRecord, setOfferToRecord] = useState(true);
  const [accounts, setAccounts] = useState<CalendarAccount[]>(INITIAL_ACCOUNTS);
  const closeRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  function toggleCalendar(accountName: string, calendarName: string) {
    setAccounts((prev) =>
      prev.map((account) =>
        account.name !== accountName
          ? account
          : {
              ...account,
              calendars: account.calendars.map((cal) =>
                cal.name === calendarName ? { ...cal, enabled: !cal.enabled } : cal,
              ),
            },
      ),
    );
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      onClose();
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
      <div ref={sheetRef} className={styles.sheet} role="dialog" aria-modal="true" aria-label="Settings">
        <div className={styles.header}>
          <div className={styles.title}>Settings</div>
          <button ref={closeRef} className={styles.close} aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

        <label className={styles.toggleRow}>
          <span>Offer to record calendar meetings</span>
          <input
            type="checkbox"
            checked={offerToRecord}
            onChange={(e) => setOfferToRecord(e.target.checked)}
          />
        </label>

        <div className={styles.sectionTitle}>Calendars</div>
        <div className={styles.calendarList}>
          {accounts.map((account) => (
            <div key={account.name} className={styles.accountGroup}>
              <div className={styles.accountName}>{account.name}</div>
              {account.calendars.map((cal) => (
                <label key={cal.name} className={styles.calendarRow}>
                  <input
                    type="checkbox"
                    checked={cal.enabled}
                    onChange={() => toggleCalendar(account.name, cal.name)}
                  />
                  <span>{cal.name}</span>
                </label>
              ))}
            </div>
          ))}
        </div>

        <button className={styles.exportButton} onClick={onExportAll}>
          Export all meetings…
        </button>
      </div>
    </div>
  );
}
