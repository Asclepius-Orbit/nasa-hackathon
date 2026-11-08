import { useEffect } from 'react';

import { useAuth } from '../../auth/AuthProvider';
import { noMonitor, notBuilt, sessionCheck } from '../../strings/common';

/** Only `unavailable` (backend down, slow or not the contract) is "no connection to the monitor". */
export function isNoMonitor(error: Error): boolean {
  return error.message === 'unavailable';
}

/**
 * `locked` is not an outage: main says nobody is signed in or the lock screen is up. Re-read the session from main so the
 * existing gate (AuthGate) sends the user to the lock or login screen.
 */
export function useSessionRecheck(error: Error | null): void {
  const { refresh } = useAuth();
  const locked = error?.message === 'locked';
  useEffect(() => {
    if (locked) refresh();
  }, [locked, refresh]);
}

/** Explicit "no connection to the monitor" state: no cache and no mock stand in for the data. */
export function NoMonitor(): React.JSX.Element {
  return (
    <div role="alert" data-testid="no-monitor">
      <p>
        <b>{noMonitor.title}.</b> {noMonitor.body}
      </p>
    </div>
  );
}

/** Explicit "not available yet" state of a screen that has no backend yet (`not_built`); the monitor itself is not at fault. */
export function NotBuilt(): React.JSX.Element {
  return (
    <div role="status" data-testid="not-built">
      <p>
        <b>{notBuilt.title}.</b> {notBuilt.body}
      </p>
    </div>
  );
}

/** The failure of a view: no-monitor for `unavailable`, not-built for `not_built`, a session re-check for `locked`, the screen's own text for anything else. */
export function LoadFailure({ error, fallback }: { error: Error; fallback: string }): React.JSX.Element {
  useSessionRecheck(error);
  if (error.message === 'locked') return <p role="status">{sessionCheck}</p>;
  if (error.message === 'not_built') return <NotBuilt />;
  return isNoMonitor(error) ? <NoMonitor /> : <p role="alert">{fallback}</p>;
}
