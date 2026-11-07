import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { createCrewStore, crewFileSchema } from '../store/crew-store';
import { createEncryptedJsonFile } from '../store/encrypted-file';
import { FakeClock, FakeSafeStorage, FlakyFs, makeTempDir } from '../store/test-fakes';
import { authStateSchema, createAuthService, LOCKOUT_MS } from './auth-service';
import { createPinHasher, type PinHasher } from './pin';

const hasher = createPinHasher({ N: 2 ** 10, r: 8, p: 1 });

let dir: string;
let cleanup: () => Promise<void>;
let safeStorage: FakeSafeStorage;
let fs: FlakyFs;
let clock: FakeClock;

beforeEach(async () => {
  ({ dir, cleanup } = await makeTempDir());
  safeStorage = new FakeSafeStorage();
  fs = new FlakyFs();
  clock = new FakeClock();
});
afterEach(async () => {
  await cleanup();
});

const statePath = (): string => join(dir, 'auth-state.bin');

/** A fresh process: new stores over the same files, empty in-memory session. */
function boot() {
  const crew = createCrewStore({
    file: createEncryptedJsonFile({ path: join(dir, 'crew.bin'), schema: crewFileSchema, safeStorage, fs }),
    hasher,
    seed: true,
  });
  const stateFile = createEncryptedJsonFile({ path: statePath(), schema: authStateSchema, safeStorage, fs });
  return createAuthService({ crew, hasher, stateFile, now: clock.now });
}

const elena = (pin: string) => ({ crewId: 'AST-01', pin });
const marc = (pin: string) => ({ crewId: 'AST-02', pin });

describe('sign in', () => {
  it('has no session at start; lock() without a session is a no-op', async () => {
    const auth = boot();
    expect(auth.current()).toBeNull();
    auth.lock();
    expect(auth.current()).toBeNull();
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: true, crewId: 'AST-01' });
  });

  it('signs in the member on the right PIN', async () => {
    const auth = boot();
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: true, crewId: 'AST-01' });
    expect(auth.current()).toEqual({ crewId: 'AST-01', locked: false });
  });

  it('keeps the session in memory only: a restart starts signed out', async () => {
    await boot().verifyPin(elena('1234'));
    expect(boot().current()).toBeNull();
  });

  it('returns a copy of the session', async () => {
    const auth = boot();
    await auth.verifyPin(elena('1234'));
    const session = auth.current();
    if (session) session.locked = true;
    expect(auth.current()).toEqual({ crewId: 'AST-01', locked: false });
  });

  it('signIn() starts a session directly (used after creating a member)', () => {
    const auth = boot();
    auth.signIn('AST-03');
    expect(auth.current()).toEqual({ crewId: 'AST-03', locked: false });
  });

  it('switches member when another one signs in while unlocked', async () => {
    const auth = boot();
    await auth.verifyPin(elena('1234'));
    expect(await auth.verifyPin(marc('1234'))).toEqual({ ok: true, crewId: 'AST-02' });
    expect(auth.current()).toEqual({ crewId: 'AST-02', locked: false });
  });

  it('returns unknown_crew for an id that does not exist, without counting an attempt', async () => {
    const auth = boot();
    expect(await auth.verifyPin({ crewId: 'AST-42', pin: '1234' })).toEqual({ ok: false, reason: 'unknown_crew' });
    expect(auth.current()).toBeNull();
  });
});

describe('timing', () => {
  /** Real scrypt underneath; only records which derivations ran (scrypt is the boundary being observed). */
  function countingHasher() {
    const calls = { verify: 0, dummy: 0 };
    const counted: PinHasher = {
      hash: (pin) => hasher.hash(pin),
      verify: (pin, encoded) => {
        calls.verify += 1;
        return hasher.verify(pin, encoded);
      },
      dummyVerify: (pin) => {
        calls.dummy += 1;
        return hasher.dummyVerify(pin);
      },
    };
    const crew = createCrewStore({
      file: createEncryptedJsonFile({ path: join(dir, 'crew.bin'), schema: crewFileSchema, safeStorage, fs }),
      hasher: counted,
      seed: true,
    });
    const stateFile = createEncryptedJsonFile({ path: statePath(), schema: authStateSchema, safeStorage, fs });
    return { calls, auth: createAuthService({ crew, hasher: counted, stateFile, now: clock.now }) };
  }

  it('spends one scrypt derivation on an unknown crew id, like a real check would', async () => {
    const { calls, auth } = countingHasher();
    await auth.verifyPin({ crewId: 'AST-42', pin: '1234' });
    expect(calls).toEqual({ verify: 0, dummy: 1 });
  });

  it('spends one scrypt derivation when another member tries the lock screen', async () => {
    const { calls, auth } = countingHasher();
    await auth.verifyPin(elena('1234'));
    auth.lock();
    await auth.verifyPin(marc('1234'));
    expect(calls).toEqual({ verify: 1, dummy: 1 });
  });
});

describe('wrong PIN and lockout', () => {
  it('counts down 2, 1 and locks for 5 minutes on the third wrong PIN', async () => {
    const auth = boot();
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 1 });
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'locked', retryAfterMs: LOCKOUT_MS });
    expect(LOCKOUT_MS).toBe(5 * 60 * 1000);
    expect(auth.current()).toBeNull();
  });

  it('refuses even the right PIN while locked, reporting the time left', async () => {
    const auth = boot();
    for (let i = 0; i < 3; i += 1) await auth.verifyPin(elena('0000'));
    clock.advance(60_000);
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'locked', retryAfterMs: 240_000 });
    clock.advance(239_999);
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'locked', retryAfterMs: 1 });
  });

  it('unlocks after 5 minutes with a fresh counter', async () => {
    const auth = boot();
    for (let i = 0; i < 3; i += 1) await auth.verifyPin(elena('0000'));
    clock.advance(LOCKOUT_MS);
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: true, crewId: 'AST-01' });
  });

  it('resets the counter on success', async () => {
    const auth = boot();
    await auth.verifyPin(elena('0000'));
    await auth.verifyPin(elena('0000'));
    await auth.verifyPin(elena('1234'));
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
  });

  it('keeps counters per crew member', async () => {
    const auth = boot();
    for (let i = 0; i < 3; i += 1) await auth.verifyPin(elena('0000'));
    expect(await auth.verifyPin(marc('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
    expect(await auth.verifyPin(marc('1234'))).toEqual({ ok: true, crewId: 'AST-02' });
  });

  it('persists counters and lockouts across restarts', async () => {
    await boot().verifyPin(elena('0000'));
    expect(await boot().verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 1 });
    await boot().verifyPin(elena('0000'));
    clock.advance(1000);
    expect(await boot().verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'locked', retryAfterMs: 299_000 });
  });

  it('cannot be bypassed by concurrent attempts', async () => {
    const auth = boot();
    const results = await Promise.all([1, 2, 3, 4].map(() => auth.verifyPin(elena('0000'))));
    const last = await auth.verifyPin(elena('1234'));
    expect(results.map((r) => (r.ok ? 'ok' : r.reason))).toEqual(['wrong_pin', 'wrong_pin', 'locked', 'locked']);
    expect(last).toEqual({ ok: false, reason: 'locked', retryAfterMs: LOCKOUT_MS });
  });

  it('re-anchors a lockout that lies further than 5 minutes ahead (clock moved back)', async () => {
    const auth = boot();
    for (let i = 0; i < 3; i += 1) await auth.verifyPin(elena('0000'));
    clock.advance(-60 * 60_000);
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'locked', retryAfterMs: LOCKOUT_MS });
    clock.advance(LOCKOUT_MS);
    expect(await boot().verifyPin(elena('1234'))).toEqual({ ok: true, crewId: 'AST-01' });
  });
});

describe('lock screen', () => {
  it('lock() locks the current session and only that member can unlock it', async () => {
    const auth = boot();
    await auth.verifyPin(elena('1234'));
    auth.lock();
    expect(auth.current()).toEqual({ crewId: 'AST-01', locked: true });
    expect(await auth.verifyPin(marc('1234'))).toEqual({ ok: false, reason: 'unknown_crew' });
    expect(auth.current()).toEqual({ crewId: 'AST-01', locked: true });
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: true, crewId: 'AST-01' });
    expect(auth.current()).toEqual({ crewId: 'AST-01', locked: false });
  });

  it('does not count another member’s attempts against them while locked', async () => {
    const auth = boot();
    await auth.verifyPin(elena('1234'));
    auth.lock();
    for (let i = 0; i < 3; i += 1) await auth.verifyPin(marc('0000'));
    auth.signIn('AST-01'); // unlock path aside: Marc's counter must be untouched
    expect(await auth.verifyPin(marc('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
  });

  it('counts wrong PINs on the lock screen and keeps it locked', async () => {
    const auth = boot();
    await auth.verifyPin(elena('1234'));
    auth.lock();
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
    expect(auth.current()).toEqual({ crewId: 'AST-01', locked: true });
  });
});

describe('fail closed', () => {
  it('is unavailable when encryption is unavailable', async () => {
    safeStorage.available = false;
    expect(await boot().verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'unavailable' });
  });

  it('is unavailable, and leaves the file alone, when the attempt state is corrupt', async () => {
    await writeFile(statePath(), 'corrupt');
    const auth = boot();
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'unavailable' });
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'unavailable' });
    expect(auth.current()).toBeNull();
    expect(await readFile(statePath(), 'utf8')).toBe('corrupt');
  });

  it('is unavailable when a wrong attempt cannot be persisted, and does not count it', async () => {
    const auth = boot();
    await auth.verifyPin(elena('1234')); // crew seeded and state loaded
    fs.failRename = true;
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'unavailable' });
    fs.failRename = false;
    expect(await auth.verifyPin(elena('0000'))).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
  });

  it('does not sign in when the counter reset cannot be persisted', async () => {
    const auth = boot();
    await auth.verifyPin(elena('0000'));
    fs.failRename = true;
    expect(await auth.verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'unavailable' });
    expect(auth.current()).toBeNull();
  });

  it('is unavailable when the crew store is unavailable', async () => {
    await writeFile(join(dir, 'crew.bin'), 'corrupt');
    expect(await boot().verifyPin(elena('1234'))).toEqual({ ok: false, reason: 'unavailable' });
  });
});
