/** Helpers shared by the services that read and write the unlocked astronaut's records. */
import type { AuthService } from '../auth/auth-service';
import { ViewsError } from '../views/views-error';

/** The unlocked astronaut, or `null` with nobody signed in or behind the lock screen. */
export function unlockedId(auth: Pick<AuthService, 'current'>): string | null {
  const session = auth.current();
  return session === null || session.locked ? null : session.crewId;
}

/** Same, for reads: rejects with `locked` instead of answering `null`. */
export function requireUnlocked(auth: Pick<AuthService, 'current'>): Promise<string> {
  const id = unlockedId(auth);
  return id === null ? Promise.reject(new ViewsError('locked')) : Promise.resolve(id);
}

/** A failed read before a write, as the write's failure: `locked` stays, anything else is logged and `unavailable`. */
export function readFailure(error: unknown, logError: (error: unknown) => void): { ok: false; reason: 'locked' | 'unavailable' } {
  if (error instanceof ViewsError && error.code === 'locked') return { ok: false, reason: 'locked' };
  logError(error);
  return { ok: false, reason: 'unavailable' };
}
