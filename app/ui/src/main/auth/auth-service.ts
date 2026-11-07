/**
 * PIN sign-in and the lock screen. Three wrong PINs lock that crew member for five minutes; counters and
 * lockouts are persisted (encrypted) so a restart does not reset them. The session itself is in memory only.
 *
 * Each attempt is counted (on disk) *before* the PIN is compared: if it cannot be recorded the answer is
 * `unavailable` and no comparison runs, so an unwritable state file never allows unlimited guessing. The
 * in-memory counters are authoritative and only advance once the disk agrees. A right PIN then clears the
 * member's counter; if that clear cannot be saved the member is still signed in (the PIN was right) and the
 * in-memory counter is cleared, while the stale count on disk can only make the next restart stricter.
 *
 * While the session is locked, only its own crew member can unlock it: a PIN for anyone else returns
 * `unknown_crew` and is not counted against them (it would let one member lock another out from the lock screen).
 */
import { z } from 'zod';

import type { AuthSession, PinResult } from '../../shared/api';
import { CREW_ID_PATTERN } from '../../shared/ipc-schema';
import type { CrewStore } from '../store/crew-store';
import type { EncryptedJsonFile } from '../store/encrypted-file';
import { createSerial } from '../store/serial';
import type { PinHasher } from './pin';

export const MAX_ATTEMPTS = 3;
export const LOCKOUT_MS = 5 * 60 * 1000;

const attemptSchema = z.strictObject({
  failures: z.int().min(1).max(MAX_ATTEMPTS),
  lockedUntil: z.number().nullable(),
});

export const authStateSchema = z.strictObject({
  version: z.literal(1),
  attempts: z.record(z.string().regex(CREW_ID_PATTERN), attemptSchema),
});

export type AuthState = z.infer<typeof authStateSchema>;
type Attempts = AuthState['attempts'];
type Attempt = z.infer<typeof attemptSchema>;

export interface AuthService {
  verifyPin(input: { crewId: string; pin: string }): Promise<PinResult>;
  /** Starts an unlocked session without a PIN check; only for a member who just chose their PIN. */
  signIn(crewId: string): void;
  lock(): void;
  current(): AuthSession | null;
}

export interface AuthServiceOptions {
  crew: Pick<CrewStore, 'findCredentials'>;
  hasher: PinHasher;
  stateFile: EncryptedJsonFile<AuthState>;
  now: () => number;
}

const UNAVAILABLE: PinResult = { ok: false, reason: 'unavailable' };
const UNKNOWN_CREW: PinResult = { ok: false, reason: 'unknown_crew' };

function without(attempts: Attempts, crewId: string): Attempts {
  return Object.fromEntries(Object.entries(attempts).filter(([id]) => id !== crewId));
}

export function createAuthService({ crew, hasher, stateFile, now }: AuthServiceOptions): AuthService {
  const serial = createSerial();
  let session: AuthSession | null = null;
  let attempts: Attempts | null = null;

  async function loadAttempts(): Promise<Attempts | null> {
    if (attempts !== null) return attempts;
    const read = await stateFile.read();
    if (read.status === 'unavailable') return null;
    attempts = read.status === 'ok' ? read.value.attempts : {};
    return attempts;
  }

  async function persist(next: Attempts): Promise<boolean> {
    if (!(await stateFile.write({ version: 1, attempts: next }))) return false;
    attempts = next;
    return true;
  }

  async function verify(crewId: string, pin: string): Promise<PinResult> {
    if (session?.locked === true && session.crewId !== crewId) {
      await hasher.dummyVerify(pin);
      return UNKNOWN_CREW;
    }
    const credentials = await crew.findCredentials(crewId);
    if (credentials.status === 'unavailable') return UNAVAILABLE;
    if (credentials.status === 'unknown') {
      await hasher.dummyVerify(pin);
      return UNKNOWN_CREW;
    }
    const current = await loadAttempts();
    if (current === null) return UNAVAILABLE;

    const time = now();
    let entry: Attempt | undefined = current[crewId];
    let lockedUntil = entry?.lockedUntil ?? null;
    if (lockedUntil !== null) {
      if (lockedUntil - time > LOCKOUT_MS) {
        // The clock moved back: never lock longer than one lockout from now.
        lockedUntil = time + LOCKOUT_MS;
        await persist({ ...current, [crewId]: { failures: MAX_ATTEMPTS, lockedUntil } });
      }
      if (time < lockedUntil) return { ok: false, reason: 'locked', retryAfterMs: lockedUntil - time };
      entry = undefined; // lockout over: fresh counter
    }

    const failures = (entry?.failures ?? 0) + 1;
    const lockedOut = failures >= MAX_ATTEMPTS;
    const counted = { ...current, [crewId]: { failures, lockedUntil: lockedOut ? time + LOCKOUT_MS : null } };
    if (!(await persist(counted))) return UNAVAILABLE;

    if (await hasher.verify(pin, credentials.pinHash)) {
      const cleared = without(counted, crewId);
      attempts = cleared; // even if saving it fails (see the header comment)
      await persist(cleared);
      session = { crewId, locked: false };
      return { ok: true, crewId };
    }
    return lockedOut
      ? { ok: false, reason: 'locked', retryAfterMs: LOCKOUT_MS }
      : { ok: false, reason: 'wrong_pin', attemptsLeft: MAX_ATTEMPTS - failures };
  }

  return {
    verifyPin: ({ crewId, pin }) => serial(() => verify(crewId, pin)),
    signIn(crewId) {
      session = { crewId, locked: false };
    },
    lock() {
      if (session !== null) session = { ...session, locked: true };
    },
    current: () => (session === null ? null : { ...session }),
  };
}
