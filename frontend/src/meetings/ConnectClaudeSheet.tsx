import { useEffect, useRef, useState } from 'react';
import { Sheet } from './Sheet';
import { PrimaryButton } from '../components/PrimaryButton';
import styles from './ConnectClaudeSheet.module.css';

interface ConnectClaudeSheetProps {
  onClose: () => void;
  lastUsed?: string | null;
}

const CLAUDE_CODE_COMMAND = 'claude mcp add speakeasy -- /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp';

const CLAUDE_DESKTOP_JSON = `{
  "mcpServers": {
    "speakeasy": {
      "command": "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy",
      "args": ["--mcp"]
    }
  }
}`;

/** Step-by-step MCP connection instructions for Claude Code and Claude Desktop. */
export function ConnectClaudeSheet({ onClose, lastUsed = '2 min ago' }: ConnectClaudeSheetProps) {
  const [codeCopied, setCodeCopied] = useState(false);
  const [jsonCopied, setJsonCopied] = useState(false);
  const [otherWaysOpen, setOtherWaysOpen] = useState(false);
  const doneRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!codeCopied) return;
    const t = window.setTimeout(() => setCodeCopied(false), 1500);
    return () => window.clearTimeout(t);
  }, [codeCopied]);

  useEffect(() => {
    if (!jsonCopied) return;
    const t = window.setTimeout(() => setJsonCopied(false), 1500);
    return () => window.clearTimeout(t);
  }, [jsonCopied]);

  function copy(text: string, mark: (value: boolean) => void) {
    void navigator.clipboard?.writeText(text).catch(() => {});
    mark(true);
  }

  return (
    <Sheet ariaLabel="Connect Claude" onClose={onClose} initialFocusRef={doneRef} className={styles.sheet}>
      <div className={styles.title}>Connect Claude</div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Claude Code</div>
        <div className={styles.codeBlock}>
          <code className={styles.code}>{CLAUDE_CODE_COMMAND}</code>
        </div>
        <button className={styles.copyButton} onClick={() => copy(CLAUDE_CODE_COMMAND, setCodeCopied)}>
          {codeCopied ? 'Copied' : 'Copy'}
        </button>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Claude Desktop</div>
        <button className={styles.installButton} onClick={() => console.log('install-in-claude-desktop')}>
          Install in Claude Desktop
        </button>
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
              <pre className={styles.code}>{CLAUDE_DESKTOP_JSON}</pre>
            </div>
            <div className={styles.desktopActions}>
              <button className={styles.copyButton} onClick={() => copy(CLAUDE_DESKTOP_JSON, setJsonCopied)}>
                {jsonCopied ? 'Copied' : 'Copy'}
              </button>
              <button className={styles.linkButton} onClick={() => console.log('reveal-config-file')}>
                Reveal config file
              </button>
            </div>
          </div>
        )}
      </div>

      <p className={styles.privacyNote}>
        Claude reads meetings only when you ask. What it reads is sent to Anthropic to answer you. Speakeasy itself
        stays offline.
      </p>

      <div className={styles.status}>{lastUsed ? `Last used by Claude: ${lastUsed}` : 'Not used yet'}</div>

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
