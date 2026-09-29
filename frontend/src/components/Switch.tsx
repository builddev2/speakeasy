import styles from './Switch.module.css';

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  ariaLabel?: string;
  ariaLabelledBy?: string;
}

/** Apple-style toggle switch (`role="switch"`), for settings-style rows. */
export function Switch({ checked, onChange, ariaLabel, ariaLabelledBy }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      className={checked ? `${styles.switch} ${styles.on}` : styles.switch}
      onClick={() => onChange(!checked)}
    >
      <span className={styles.thumb} />
    </button>
  );
}
