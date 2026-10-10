import { useRef, useState } from 'react';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import type { Appearance, MeetingSettings } from '../mock/meetings';
import { Sheet } from './Sheet';
import { Switch } from '../components/Switch';
import { PrimaryButton } from '../components/PrimaryButton';
import styles from './SettingsSheet.module.css';

interface SettingsSheetProps {
  settings: MeetingSettings;
  /** "No calendars found." only makes sense once access is granted. */
  calendarConnected: boolean;
  onChange: (patch: { offerToRecord?: boolean; detectCalls?: boolean; appearance?: Appearance; identifyVoices?: boolean; startAtLogin?: boolean; calendars?: Record<string, boolean>; userName?: string; userAliases?: string[] }) => void;
  onClose: () => void;
  onExportAll: () => void;
  exportStatus: string;
  onRevealExport: () => void;
  exportReady: boolean;
  onBackup: () => void;
  onRestore: () => void;
  backupStatus: string;
  restoreStatus: string;
  backupReady: boolean;
  onRevealBackup: () => void;
  libraryBusy: boolean;
}

/** Meetings section of Settings: record prompts, appearance, calendar checklist, export. */
export function SettingsSheet({ settings, calendarConnected, onChange, onClose, onExportAll, exportStatus, onRevealExport, exportReady, onBackup, onRestore, backupStatus, restoreStatus, backupReady, onRevealBackup, libraryBusy }: SettingsSheetProps) {
  const { offerToRecord, detectCalls, appearance, identifyVoices, startAtLogin, accounts } = settings;
  const doneRef = useRef<HTMLButtonElement>(null);
  const [nameDraft, setNameDraft] = useState(settings.userName);
  const [aliasDraft, setAliasDraft] = useState(settings.userAliases.join(', '));

  return (
    <Sheet ariaLabel="Settings" onClose={onClose} initialFocusRef={doneRef} className={styles.sheet}>
      <div className={styles.title}>Settings</div>

      <div className={styles.sectionTitle}>Record prompts</div>
      <div className={styles.toggleRow}>
        <span id="offer-to-record-label">
          Offer to record calendar meetings
          <span className={styles.hint}>When an event with other people starts</span>
        </span>
        <Switch checked={offerToRecord} onChange={(value) => onChange({ offerToRecord: value })} ariaLabelledBy="offer-to-record-label" />
      </div>
      <div className={styles.toggleRow}>
        <span id="detect-calls-label">
          Offer to record calls in other apps
          <span className={styles.hint}>Also asks to stop when the call ends</span>
        </span>
        <Switch checked={detectCalls} onChange={(value) => onChange({ detectCalls: value })} ariaLabelledBy="detect-calls-label" />
      </div>

      <div className={styles.sectionTitle}>Appearance</div>
      <div className={styles.segmented} role="radiogroup" aria-label="Appearance">
        {(['system', 'light', 'dark'] as const).map((choice) => (
          <button
            key={choice}
            role="radio"
            aria-checked={appearance === choice}
            className={appearance === choice ? `${styles.segment} ${styles.segmentOn}` : styles.segment}
            onClick={() => onChange({ appearance: choice })}
          >
            {choice === 'system' ? 'System' : choice === 'light' ? 'Light' : 'Dark'}
          </button>
        ))}
      </div>

      <div className={styles.sectionTitle}>Recording</div>
      <div className={styles.toggleRow}>
        <span id="identify-voices-label">
          Identify enrolled voices
          <span className={styles.hint}>Names people whose voices you've trained</span>
        </span>
        <Switch checked={identifyVoices} onChange={(value) => onChange({ identifyVoices: value })} ariaLabelledBy="identify-voices-label" />
      </div>

      <div className={styles.sectionTitle}>Action items</div>
      <label className={styles.fieldRow}>
        <span>Your name</span>
        <input {...NO_AUTOCORRECT} className={styles.textField} value={nameDraft} maxLength={80}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={() => { if (nameDraft.trim() !== settings.userName) onChange({ userName: nameDraft.trim() }); }} />
      </label>
      <label className={styles.fieldRow}>
        <span>Other names</span>
        <input {...NO_AUTOCORRECT} className={styles.textField} value={aliasDraft} placeholder="Comma-separated"
          onChange={(e) => setAliasDraft(e.target.value)}
          onBlur={() => {
            const aliases = aliasDraft.split(',').map((s) => s.trim()).filter(Boolean);
            if (aliases.join('\n') !== settings.userAliases.join('\n')) onChange({ userAliases: aliases });
          }} />
      </label>
      <div className={styles.hint}>Items whose owner matches any of these are yours. Anyone who shares your first name also matches.</div>

      <div className={styles.sectionTitle}>General</div>
      <div className={styles.toggleRow}>
        <span id="start-at-login-label">
          Start at login
          {startAtLogin === null && <span className={styles.hint}>Available in the installed app</span>}
        </span>
        <Switch checked={startAtLogin === true} disabled={startAtLogin === null} onChange={(value) => onChange({ startAtLogin: value })} ariaLabelledBy="start-at-login-label" />
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

      <button className={styles.exportButton} onClick={onExportAll} disabled={libraryBusy}>
        Export all meetings…
      </button>
      {exportStatus && <div role="status" className={styles.hint}>{exportStatus}</div>}
      {exportReady && <button className={styles.exportButton} onClick={onRevealExport}>Reveal export folder</button>}
      <button className={styles.exportButton} onClick={onBackup} disabled={libraryBusy}>
        Back up library…
      </button>
      {backupStatus && <div role="status" className={styles.hint}>{backupStatus}</div>}
      {backupReady && <button className={styles.exportButton} onClick={onRevealBackup}>Reveal backup file</button>}
      <button className={styles.exportButton} onClick={onRestore} disabled={libraryBusy}>
        Restore library…
      </button>
      {restoreStatus && <div role="status" className={styles.hint}>{restoreStatus}</div>}

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
