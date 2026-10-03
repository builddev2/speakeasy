import styles from './Switch.module.css';

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  ariaLabel?: string;
  ariaLabelledBy?: string;
  disabled?: boolean;
}

/** Apple-style toggle switch (`role="switch"`), for settings-style rows. */
export function Switch({ checked, onChange, ariaLabel, ariaLabelledBy, disabled }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      className={checked ? `${styles.switch} ${styles.on}` : styles.switch}
      onClick={() => onChange(!checked)}
    >
      <span className={styles.thumb} />
    </button>
  );
}
