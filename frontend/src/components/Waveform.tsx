import styles from './Waveform.module.css';

const BAR_COUNT = 26;

interface Bar {
  key: number;
  style: {
    background: string;
    boxShadow: string;
    animation: string;
  };
}

function buildBars(): Bar[] {
  return Array.from({ length: BAR_COUNT }, (_, i) => {
    const hue = Math.round((i * 300) / (BAR_COUNT - 1));
    const duration = (0.9 + (i % 4) * 0.13).toFixed(2);
    const delay = ((i % 6) * 0.1 + i * 0.014).toFixed(2);
    return {
      key: i,
      style: {
        background: `linear-gradient(180deg, rgba(255,255,255,0.85), hsl(${hue} 92% 62%) 42%, hsl(${hue} 88% 48%))`,
        boxShadow: `0 0 7px 0 hsl(${hue} 95% 60% / 0.75), inset 0 0 2px 0 rgba(255,255,255,0.6)`,
        animation: `pill ${duration}s ${delay}s ease-in-out infinite`,
      },
    };
  });
}

export function Waveform() {
  const bars = buildBars();
  return (
    <div className={styles.hero}>
      <div className={styles.bloom} />
      <div className={styles.bars}>
        {bars.map((bar) => (
          <span key={bar.key} className={styles.bar} style={bar.style} />
        ))}
      </div>
    </div>
  );
}
