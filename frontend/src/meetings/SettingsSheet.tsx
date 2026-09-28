import { useRef, useState } from 'react';
import { Sheet } from './Sheet';
import { Switch } from '../components/Switch';
import { PrimaryButton } from '../components/PrimaryButton';
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

/** Meetings section of Settings: recording offer toggle, calendar checklist, export. */
export function SettingsSheet({ onClose, onExportAll }: SettingsSheetProps) {
  const [offerToRecord, setOfferToRecord] = useState(true);
  const [accounts, setAccounts] = useState<CalendarAccount[]>(INITIAL_ACCOUNTS);
  const doneRef = useRef<HTMLButtonElement>(null);

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

  return (
    <Sheet ariaLabel="Settings" onClose={onClose} initialFocusRef={doneRef} className={styles.sheet}>
      <div className={styles.title}>Settings</div>

      <div className={styles.toggleRow}>
        <span id="offer-to-record-label">Offer to record calendar meetings</span>
        <Switch checked={offerToRecord} onChange={setOfferToRecord} ariaLabelledBy="offer-to-record-label" />
      </div>

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

      <div className={styles.footer}>
        <div className={styles.doneWrap}>
          <PrimaryButton ref={doneRef} onClick={onClose}>
            Done
          </PrimaryButton>
        </div>
      </div>
    </Sheet>
  );
}
