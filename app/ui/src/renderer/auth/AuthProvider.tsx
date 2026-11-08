import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import type { PinResult } from '../../shared/api';
import { useApi } from '../api/ApiContext';

export type AuthStatus = 'loading' | 'signed-out' | 'locked' | 'signed-in';

export interface AuthApi {
  status: AuthStatus;
  /** The crew member of the session (also while locked); `null` when signed out. */
  crewId: string | null;
  /** Applies a PIN result: only an ok result signs in (also used to unlock). */
  signIn: (result: PinResult) => void;
  /** Locks through main, then shows the lock screen. */
  lock: () => Promise<void>;
  /** Re-reads the session from main (for example after a view answered `locked`): the gate then sends the user to the lock or login screen. */
  refresh: () => void;
  /** Drops the local session only (used by the dev Mock screens menu); main keeps its own. */
  signOut: () => void;
}

interface AuthState {
  status: AuthStatus;
  crewId: string | null;
}

const AuthContext = createContext<AuthApi | null>(null);

/** Where the router must send someone in this state, or `null` when they may stay. */
export function redirectFor(status: AuthStatus, pathname: string): string | null {
  if (status === 'loading') return null;
  if (status === 'signed-out') return pathname === '/login' ? null : '/login';
  if (status === 'locked') return pathname === '/lock' ? null : '/lock';
  return pathname === '/login' || pathname === '/lock' ? '/today' : null;
}

/** Reads the session from main on mount and tracks sign-in and lock. Anything unreadable counts as signed out. */
export function AuthProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const api = useApi();
  const [state, setState] = useState<AuthState>({ status: 'loading', crewId: null });

  useEffect(() => {
    let live = true;
    api.auth
      .current()
      .then((session) => {
        if (!live) return;
        if (session === null) setState({ status: 'signed-out', crewId: null });
        else setState({ status: session.locked ? 'locked' : 'signed-in', crewId: session.crewId });
      })
      .catch(() => {
        if (live) setState({ status: 'signed-out', crewId: null });
      });
    return () => {
      live = false;
    };
  }, [api]);

  const signIn = useCallback((result: PinResult) => {
    if (result.ok) setState({ status: 'signed-in', crewId: result.crewId });
  }, []);

  const lock = useCallback(async () => {
    await api.auth.lock();
    setState((prev) => ({ ...prev, status: 'locked' }));
  }, [api]);

  const refresh = useCallback(() => {
    api.auth.current().then(
      (session) => {
        if (session === null) setState({ status: 'signed-out', crewId: null });
        else setState({ status: session.locked ? 'locked' : 'signed-in', crewId: session.crewId });
      },
      () => {
        setState({ status: 'signed-out', crewId: null });
      },
    );
  }, [api]);

  const signOut = useCallback(() => {
    setState({ status: 'signed-out', crewId: null });
  }, []);

  const value = useMemo<AuthApi>(() => ({ ...state, signIn, lock, refresh, signOut }), [state, signIn, lock, refresh, signOut]);
  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthApi {
  const value = useContext(AuthContext);
  if (value === null) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
