import styles from './ChipGroup.module.css';
import { cx } from './cx';
import pick from './Pick.module.css';

export interface ChipOption<Value extends string> {
  value: Value;
  label: string;
}

export interface ChipGroupProps<Value extends string> {
  /** Accessible name of the group; usually the question. */
  label: string;
  options: readonly ChipOption<Value>[];
  selected: readonly Value[];
  onChange: (next: Value[]) => void;
  /** `flag` paints chosen chips in the alert red (warning symptoms). */
  tone?: 'default' | 'flag';
}

/** Multi-select toggle chips; each chip is a button with `aria-pressed`. */
export function ChipGroup<Value extends string>({ label, options, selected, onChange, tone = 'default' }: ChipGroupProps<Value>): React.JSX.Element {
  return (
    <div role="group" aria-label={label} data-tone={tone} className={styles.chips}>
      {options.map((option) => {
        const on = selected.includes(option.value);
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            className={cx(pick.pick, styles.chip)}
            onClick={() => {
              onChange(on ? selected.filter((v) => v !== option.value) : [...selected, option.value]);
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
