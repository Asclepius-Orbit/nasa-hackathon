import { MockSkipLogin } from '../../components/layout/MockSkipLogin';
import { Button } from '../../components/ui/Button';
import { useUrgent } from '../urgent/UrgentContext';

/** PLACEHOLDER: the lock screen is built later; replace the inside of this folder only. Urgent must stay available while locked. */
export function LockView(): React.JSX.Element {
  const { open } = useUrgent();
  return (
    <section aria-labelledby="lock-title" data-testid="lock-view">
      <h1 id="lock-title">Enter your crew PIN</h1>
      <Button
        variant="urgent"
        onClick={() => {
          open();
        }}
      >
        Urgent symptom, works while locked
      </Button>
      <MockSkipLogin />
    </section>
  );
}
