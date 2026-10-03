import type { CSSProperties, ReactNode } from 'react';
import { bridge } from '../bridge';
import styles from './GlassPanel.module.css';

interface GlassPanelProps {
  width: number;
  height: number;
  children: ReactNode;
}

export function GlassPanel({ width, height, children }: GlassPanelProps) {
  // `?fit` lets the browser preview resize like the real window.
  const fit = bridge.embedded || new URLSearchParams(window.location.search).has('fit');
  const style: CSSProperties = fit
    ? { width: '100vw', height: '100vh' }
    : { width, height };
  const className = bridge.embedded
    ? `${styles.panel} ${styles.embeddedPanel}`
    : styles.panel;
  return (
    <div className={className} style={style}>
      {children}
    </div>
  );
}
