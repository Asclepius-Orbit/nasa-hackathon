import { Navigate, Outlet, useLocation } from 'react-router';

import { redirectFor, useAuth } from './AuthProvider';

/** Route guard: signed-out goes to /login, locked to /lock, signed-in leaves /login and /lock. Renders nothing of the app while loading. */
export function AuthGate(): React.JSX.Element {
  const { status } = useAuth();
  const { pathname } = useLocation();
  if (status === 'loading') return <p role="status">Loading</p>;
  const target = redirectFor(status, pathname);
  if (target !== null) return <Navigate to={target} replace />;
  return <Outlet />;
}
