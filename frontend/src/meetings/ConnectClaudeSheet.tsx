import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
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

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>('input, button, [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => !el.hasAttribute('disabled'));
}

/** Step-by-step MCP connection instructions for Claude Code and Claude Desktop. */
export function ConnectClaudeSheet({ onClose, lastUsed = '2 min ago' }: ConnectClaudeSheetProps) {
  const [codeCopied, setCodeCopied] = useState(false);
  const [jsonCopied, setJsonCopied] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

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
      <div ref={sheetRef} className={styles.sheet} role="dialog" aria-modal="true" aria-label="Connect Claude">
        <div className={styles.header}>
          <div className={styles.title}>Connect Claude</div>
          <button ref={closeRef} className={styles.close} aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

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
          <div className={styles.otherWays}>Other ways to connect</div>
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

        <p className={styles.privacyNote}>
          Claude reads meetings only when you ask. What it reads is sent to Anthropic to answer you. Speakeasy itself
          stays offline.
        </p>

        <div className={styles.status}>{lastUsed ? `Last used by Claude: ${lastUsed}` : 'Not used yet'}</div>
      </div>
    </div>
  );
}
