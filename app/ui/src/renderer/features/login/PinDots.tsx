import { login } from '../../strings/login';
import { PIN_LENGTH } from './usePinEntry';
import styles from './PinDots.module.css';

/** Four dots for the PIN typed so far; never the digits. A visually hidden count keeps it usable with a screen reader. */
export function PinDots({ length }: { length: number }): React.JSX.Element {
  return (
    <div className={styles.wrap}>
      <div className={styles.dots} aria-hidden="true">
        {Array.from({ length: PIN_LENGTH }, (_, i) => (
          <i key={i} data-on={i < length} />
        ))}
      </div>
      <span className={styles.sr} role="status">
        {login.pinProgress(length)}
      </span>
    </div>
  );
}
