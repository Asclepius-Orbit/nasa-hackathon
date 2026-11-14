import { useApi } from '../../api/ApiContext';
import { useAuth } from '../../auth/AuthProvider';

/** Development-only shortcut (the mock's "skip login or lock"): signs in as the first crew member with no PIN. Renders nothing in production. */
export function MockSkipLogin(): React.JSX.Element | null {
  const api = useApi();
  const { signIn } = useAuth();
  if (!import.meta.env.DEV) return null;
  return (
    <button
      type="button"
      onClick={() => {
        void api.crew.list().then((list) => {
          const first = list[0];
          if (first) signIn({ ok: true, crewId: first.id });
        });
      }}
    >
      Mock: skip login or lock
    </button>
  );
}
