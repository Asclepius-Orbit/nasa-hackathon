import { cx } from './cx';
import styles from './Meter.module.css';

export interface MeterProps {
  /** Accessible name. */
  label: string;
  value: number;
  max: number;
  /** Thinner bar for dense lists. */
  thin?: boolean;
}

/** Progress bar. The fill is clamped to 0-100 % and an empty scale reads as 0, never as full. */
export function Meter({ label, value, max, thin = false }: MeterProps): React.JSX.Element {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} className={cx(styles.meter, thin && styles.thin)}>
      <span data-testid="meter-fill" className={styles.fill} style={{ width: `${String(percent)}%` }} />
    </div>
  );
}
