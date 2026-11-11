import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import type { FormErrors } from '../profile/fields';
import styles from './Form.module.css';

export interface SaveBarProps {
  label: string;
  canSave: boolean;
  /** Short status text next to the button (what is still to answer). */
  note: string;
  onSave: () => void;
  /** Shown instead of the button state once saved. */
  savedMessage: string | null;
  /** The save is on its way: the button waits and shows `busyLabel`. */
  busy?: boolean;
  busyLabel?: string;
  /** Why nothing was saved: a general `failure`, the `errors` that matched no question, and `fixMarked` when some did. */
  problem?: { failure: string | null; errors: FormErrors; fixMarked: string };
}

/** Sticky bottom bar of the forms: the Save button, what is still missing, why a save failed, and the confirmation. */
export function SaveBar({ label, canSave, note, onSave, savedMessage, busy = false, busyLabel = label, problem }: SaveBarProps): React.JSX.Element {
  const marked = problem !== undefined && Object.keys(problem.errors.byPointer).length > 0;
  const general = problem?.errors.general ?? [];
  const failure = problem === undefined ? null : (problem.failure ?? (marked ? problem.fixMarked : null));
  return (
    <div className={styles.saveBar}>
      <Button disabled={!canSave || busy} onClick={onSave}>
        {busy ? busyLabel : label}
      </Button>
      <small>{note}</small>
      {(failure !== null || general.length > 0) && (
        <Alert>
          {failure}
          {general.map((line) => (
            <span key={line} className={styles.line}>
              {line}
            </span>
          ))}
        </Alert>
      )}
      {savedMessage !== null && (
        <Alert variant="info" role="status">
          {savedMessage}
        </Alert>
      )}
    </div>
  );
}
