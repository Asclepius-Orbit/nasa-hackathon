import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import styles from './Form.module.css';

export interface SaveBarProps {
  label: string;
  canSave: boolean;
  /** Short status text next to the button (what is still to answer). */
  note: string;
  onSave: () => void;
  /** Shown instead of the button state once saved. */
  savedMessage: string | null;
}

/** Sticky bottom bar of the forms: the Save button, what is still missing, and the confirmation. */
export function SaveBar({ label, canSave, note, onSave, savedMessage }: SaveBarProps): React.JSX.Element {
  return (
    <div className={styles.saveBar}>
      <Button disabled={!canSave} onClick={onSave}>
        {label}
      </Button>
      <small>{note}</small>
      {savedMessage !== null && (
        <Alert variant="info" role="status">
          {savedMessage}
        </Alert>
      )}
    </div>
  );
}
