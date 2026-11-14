import type { StepStatus } from '../../data/types';
import styles from './Stepper.module.css';

export interface StepperStep<Id extends string> {
  id: Id;
  label: string;
  status: StepStatus;
}

export interface StepperProps<Id extends string> {
  /** Accessible name of the list. */
  label: string;
  steps: readonly StepperStep<Id>[];
  /** Step currently shown in the detail area (underlined). */
  viewing?: Id;
  /** When given, every step is a button that reports its id; otherwise the list is read-only. */
  onSelect?: (id: Id) => void;
}

/** Numbered protocol steps: done (check), now (current, `aria-current=step`) and to do. */
export function Stepper<Id extends string>({ label, steps, viewing, onSelect }: StepperProps<Id>): React.JSX.Element {
  return (
    <ol aria-label={label} className={styles.steps}>
      {steps.map((step) => (
        <li
          key={step.id}
          data-status={step.status}
          data-viewing={viewing === step.id}
          aria-current={onSelect === undefined && step.status === 'now' ? 'step' : undefined}
        >
          {onSelect === undefined ? (
            step.label
          ) : (
            <button
              type="button"
              aria-current={step.status === 'now' ? 'step' : undefined}
              onClick={() => {
                onSelect(step.id);
              }}
            >
              {step.label}
            </button>
          )}
        </li>
      ))}
    </ol>
  );
}
