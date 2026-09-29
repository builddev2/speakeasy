import { useEffect, useRef, useState } from 'react';
import { Sheet } from './Sheet';
import { PrimaryButton } from '../components/PrimaryButton';
import type { ClaudeSetupInfo } from '../mock/meetings';
import styles from './ConnectClaudeSheet.module.css';

interface ConnectClaudeSheetProps {
  onClose: () => void;
  info: ClaudeSetupInfo | null;
  onCopy: (text: string) => Promise<void>;
  onInstall: () => Promise<void>;
  onRevealConfig: () => Promise<void>;
}

const ERRORS: Record<string, string> = {
  extension_unavailable: 'The one-click extension is not available in this copy of Speakeasy.',
  claude_desktop_not_found: 'Claude Desktop does not seem to be installed (no settings folder found).',
};

function message(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return ERRORS[text] ?? 'That did not work. Try again.';
}

/** Step-by-step MCP connection instructions for Claude Code and Claude Desktop. */
export function ConnectClaudeSheet({ onClose, info, onCopy, onInstall, onRevealConfig }: ConnectClaudeSheetProps) {
  const [copied, setCopied] = useState<'code' | 'json' | null>(null);
  const [otherWaysOpen, setOtherWaysOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(null), 1500);
    return () => window.clearTimeout(t);
  }, [copied]);

  function run(action: () => Promise<void>, after?: () => void) {
    setError(null);
    action().then(after, (err) => setError(message(err)));
  }

  const command = info?.command ?? 'Loading…';
  const desktopJson = info?.desktopJson ?? 'Loading…';

  return (
    <Sheet ariaLabel="Connect Claude" onClose={onClose} initialFocusRef={doneRef} className={styles.sheet}>
      <div className={styles.title}>Connect Claude</div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Claude Code</div>
        <div className={styles.codeBlock}>
          <code className={styles.code}>{command}</code>
        </div>
        <button className={styles.copyButton} disabled={!info}
                onClick={() => run(() => onCopy(command), () => setCopied('code'))}>
          {copied === 'code' ? 'Copied' : 'Copy'}
        </button>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Claude Desktop</div>
        <button className={styles.installButton} disabled={!info?.extensionAvailable}
                onClick={() => run(onInstall)}>
          Install in Claude Desktop
        </button>
        {info?.extensionNote && <p className={styles.note}>{info.extensionNote}</p>}
        <button
          className={styles.disclosure}
          aria-expanded={otherWaysOpen}
          aria-controls="connect-claude-other-ways"
          onClick={() => setOtherWaysOpen((v) => !v)}
        >
          <span className={otherWaysOpen ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} aria-hidden="true">
            ›
          </span>
          Other ways to connect
        </button>
        {otherWaysOpen && (
          <div id="connect-claude-other-ways" className={styles.otherWaysPanel}>
            <div className={styles.codeBlock}>
              <pre className={styles.code}>{desktopJson}</pre>
            </div>
            <div className={styles.desktopActions}>
              <button className={styles.copyButton} disabled={!info}
                      onClick={() => run(() => onCopy(desktopJson), () => setCopied('json'))}>
                {copied === 'json' ? 'Copied' : 'Copy'}
              </button>
              <button className={styles.linkButton} onClick={() => run(onRevealConfig)}>
                Reveal config file
              </button>
            </div>
          </div>
        )}
      </div>

      {error && <p className={styles.error} role="alert">{error}</p>}

      <p className={styles.privacyNote}>
        Claude reads meetings only when you ask. What it reads is sent to Anthropic to answer you. Speakeasy itself
        stays offline.
      </p>

      <div className={styles.status}>
        {info?.lastUsed ? `Last used by Claude: ${info.lastUsed}` : 'Not used by Claude yet'}
        {' · '}You can turn the extension off in Claude Desktop → Settings → Extensions.
      </div>

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
