import { useId } from 'react';

import pick from '../../components/ui/Pick.module.css';
import { cx } from '../../components/ui/cx';
import styles from './Profile.module.css';

/** What a form shows after a save attempt: refusals by JSON pointer, plus those that matched no field. */
export interface FormErrors {
  byPointer: Record<string, string>;
  general: string[];
}

export const NO_ERRORS: FormErrors = { byPointer: {}, general: [] };

/** Splits the backend's field refusals into those a field of this form can show and the rest. */
export function sortFieldErrors(fields: readonly { pointer: string; message: string }[], known: readonly string[]): FormErrors {
  const byPointer: Record<string, string> = {};
  const general: string[] = [];
  for (const { pointer, message } of fields) {
    if (known.includes(pointer)) byPointer[pointer] = message;
    else general.push(`${pointer}: ${message}`);
  }
  return { byPointer, general };
}

/** A decimal typed with a point or a comma; `null` when it is not a finite number. */
export function parseNumber(text: string): number | null {
  const value = Number(text.trim().replace(',', '.'));
  return text.trim() === '' || !Number.isFinite(value) ? null : value;
}

export function TextField({
  label,
  value,
  onChange,
  error,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  error: string | undefined;
  type?: 'text' | 'date';
}): React.JSX.Element {
  const id = useId();
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <input
        id={id}
        className={styles.input}
        type={type}
        inputMode={type === 'text' ? 'decimal' : undefined}
        value={value}
        aria-invalid={error !== undefined}
        aria-describedby={error === undefined ? undefined : `${id}-error`}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {error !== undefined && (
        <p id={`${id}-error`} className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}

export function ChoiceField<Value extends string>({
  label,
  options,
  value,
  onChange,
  error,
}: {
  label: string;
  options: readonly { value: Value; label: string }[];
  value: Value | null;
  onChange: (next: Value) => void;
  error: string | undefined;
}): React.JSX.Element {
  const id = useId();
  return (
    <div className={styles.field}>
      <span id={id} className={styles.label}>
        {label}
      </span>
      <div className={styles.picks} role="group" aria-labelledby={id} aria-describedby={error === undefined ? undefined : `${id}-error`}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={cx(pick.pick, styles.pick)}
            aria-pressed={option.value === value}
            onClick={() => {
              onChange(option.value);
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
      {error !== undefined && (
        <p id={`${id}-error`} className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}
