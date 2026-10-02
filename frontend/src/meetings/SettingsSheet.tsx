import { useRef } from 'react';
import type { Appearance, MeetingSettings } from '../mock/meetings';
import { Sheet } from './Sheet';
import { Switch } from '../components/Switch';
import { PrimaryButton } from '../components/PrimaryButton';
import styles from './SettingsSheet.module.css';

interface SettingsSheetProps {
  settings: MeetingSettings;
  /** "No calendars found." only makes sense once access is granted. */
  calendarConnected: boolean;
  onChange: (patch: { offerToRecord?: boolean; detectCalls?: boolean; appearance?: Appearance; calendars?: Record<string, boolean> }) => void;
  onClose: () => void;
  onExportAll: () => void;
}

/** Meetings section of Settings: recording offer toggle, calendar checklist, export. */
export function SettingsSheet({ settings, calendarConnected, onChange, onClose, onExportAll }: SettingsSheetProps) {
  const { offerToRecord, detectCalls, accounts } = settings;
  const doneRef = useRef<HTMLButtonElement>(null);

  return (
    <Sheet ariaLabel="Settings" onClose={onClose} initialFocusRef={doneRef} className={styles.sheet}>
      <div className={styles.title}>Settings</div>

      <div className={styles.toggleRow}>
        <span id="offer-to-record-label">Offer to record calendar meetings</span>
        <Switch checked={offerToRecord} onChange={(value) => onChange({ offerToRecord: value })} ariaLabelledBy="offer-to-record-label" />
      </div>

      <div className={styles.toggleRow}>
        <span id="detect-calls-label">Offer to record calls in other apps</span>
        <Switch checked={detectCalls} onChange={(value) => onChange({ detectCalls: value })} ariaLabelledBy="detect-calls-label" />
      </div>

      <div className={styles.sectionTitle}>Calendars</div>
      <div className={styles.calendarList}>
        {accounts.length === 0 && calendarConnected && <div className={styles.accountName}>No calendars found.</div>}
        {accounts.map((account) => (
          <div key={account.name} className={styles.accountGroup}>
            <div className={styles.accountName}>{account.name}</div>
            {account.calendars.map((cal) => (
              <label key={cal.id} className={styles.calendarRow}>
                <input
                  type="checkbox"
                  checked={cal.enabled}
                  onChange={() => onChange({ calendars: { [cal.id]: !cal.enabled } })}
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
