import styles from './Keypad.module.css';

export interface KeypadProps {
  onDigit: (digit: string) => void;
  onDelete: () => void;
  onEnter: () => void;
  /** Disables every key (e.g. until a name is picked). */
  disabled?: boolean;
}

const ROWS = [['1', '2', '3'], ['4', '5', '6'], ['7', '8', '9']] as const;

/** Dark 3x4 PIN pad for login and lock: digits, Delete, Enter. Meant for a dark background. */
export function Keypad({ onDigit, onDelete, onEnter, disabled = false }: KeypadProps): React.JSX.Element {
  const digit = (d: string): React.JSX.Element => (
    <button
      key={d}
      type="button"
      disabled={disabled}
      onClick={() => {
        onDigit(d);
      }}
    >
      {d}
    </button>
  );
  return (
    <div className={styles.keypad}>
      {ROWS.flat().map(digit)}
      <button type="button" disabled={disabled} className={styles.small} aria-label="Delete last digit" onClick={onDelete}>
        Delete
      </button>
      {digit('0')}
      <button type="button" disabled={disabled} className={styles.small} onClick={onEnter}>
        Enter
      </button>
    </div>
  );
}
