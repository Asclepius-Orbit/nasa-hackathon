import pick from '../../components/ui/Pick.module.css';
import styles from './Form.module.css';

export interface PickOption {
  label: string;
}

/** Optional single-choice row of toggle buttons; choosing the pressed one again clears it. */
export function PickRow({
  label,
  options,
  selected,
  onSelect,
}: {
  label: string;
  options: readonly PickOption[];
  /** Index of the chosen option, or `null`. */
  selected: number | null;
  onSelect: (index: number | null) => void;
}): React.JSX.Element {
  return (
    <div role="group" aria-label={label} className={styles.rowPicks}>
      {options.map((option, index) => (
        <button
          key={option.label}
          type="button"
          aria-pressed={selected === index}
          className={pick.pick}
          onClick={() => {
            onSelect(selected === index ? null : index);
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
