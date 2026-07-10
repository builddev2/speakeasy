import type { CSSProperties, ReactNode } from 'react';
import styles from './GlassPanel.module.css';

interface GlassPanelProps {
  width: number;
  height: number;
  children: ReactNode;
}

export function GlassPanel({ width, height, children }: GlassPanelProps) {
  const style: CSSProperties = { width, height };
  return (
    <div className={styles.panel} style={style}>
      {children}
    </div>
  );
}
