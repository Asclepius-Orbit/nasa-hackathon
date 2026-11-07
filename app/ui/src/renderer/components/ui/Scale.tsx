import { useId } from 'react';

import pick from './Pick.module.css';
import styles from './Scale.module.css';

export interface ScaleProps {
  /** Accessible name of the group; usually the question. */
  label: string;
  /** Chosen number, or `null` while unanswered (an unanswered scale is never read as 0). */
  value: number | null;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  /** Caption under the first button, e.g. `0, no pain`. */
  lowLabel?: string;
  /** Caption under the last button. */
  highLabel?: string;
}

/** 0-10 rating as native radios: Tab enters the group, arrow keys move and choose. */
export function Scale({ label, value, onChange, min = 0, max = 10, lowLabel, highLabel }: ScaleProps): React.JSX.Element {
  const name = useId();
  const numbers = Array.from({ length: max - min + 1 }, (_, i) => min + i);
  return (
    <div>
      <div role="radiogroup" aria-label={label} className={styles.scale} style={{ '--cols': numbers.length } as React.CSSProperties}>
        {numbers.map((n) => (
          <label key={n} className={styles.option}>
            <input
              type="radio"
              name={name}
              value={n}
              aria-label={String(n)}
              checked={value === n}
              onChange={() => {
                onChange(n);
              }}
              className={pick.visuallyHidden}
            />
            <span className={pick.pick} aria-hidden="true">
              {n}
            </span>
          </label>
        ))}
      </div>
      {(lowLabel !== undefined || highLabel !== undefined) && (
        <div className={styles.ends}>
          <span>{lowLabel}</span>
          <span>{highLabel}</span>
        </div>
      )}
    </div>
  );
}
