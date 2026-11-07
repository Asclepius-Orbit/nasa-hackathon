import { MockSkipLogin } from '../../components/layout/MockSkipLogin';
import { Button } from '../../components/ui/Button';
import { useUrgent } from '../urgent/UrgentContext';

/** PLACEHOLDER: the login screen is built later; replace the inside of this folder only. Urgent must stay available with no session. */
export function LoginView(): React.JSX.Element {
  const { open } = useUrgent();
  return (
    <section aria-labelledby="login-title" data-testid="login-view">
      <h2>Asclepius Orbit, on-board spine monitoring</h2>
      <h1 id="login-title">Who is using this tablet?</h1>
      <Button
        variant="urgent"
        onClick={() => {
          open();
        }}
      >
        Urgent symptom, no login needed
      </Button>
      <MockSkipLogin />
    </section>
  );
}
