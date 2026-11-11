import { describe, expect, it } from 'vitest';

import type { AstronautProfile, AuthSession, DailyContext } from '../../shared/api';
import { ViewsError } from '../views/views-error';
import { createDailyContextService, deriveDailyContext, utcDay } from './daily-context-service';
import { FakeBackend } from './test-fakes';

const ID = 'AST-01';
const UNLOCKED: AuthSession = { crewId: ID, locked: false };
// 2026-11-11 23:30 UTC: still the 11th in UTC whatever the local zone is.
const NOW = Date.UTC(2026, 10, 11, 23, 30);
const TODAY = '2026-11-11';

const profile = (launch: string | null | undefined, landing: string | null = null): AstronautProfile =>
  ({
    schema_version: '1.0.0',
    astronaut_id: ID,
    updated_at: '2026-11-01T00:00:00+00:00',
    date_of_birth: '1985-04-02',
    sex_at_birth: 'female',
    anthropometry: { height_standing_cm: 170, body_mass_kg: 62 },
    ...(launch === undefined ? {} : { launch_date: launch }),
    landing_date: landing,
  });

describe('utcDay', () => {
  it('is the UTC calendar day', () => {
    expect(utcDay(NOW)).toBe(TODAY);
    expect(utcDay(Date.UTC(2026, 0, 1, 0, 0))).toBe('2026-01-01');
  });
});

describe('deriveDailyContext (same rules as the backend mission.py)', () => {
  it.each([
    ['30 days before launch', '2026-12-11', null, { phase: 'preflight', gravity_environment: 'earth_1g', mission_day: -30 }],
    ['the day before launch', '2026-11-12', null, { phase: 'preflight', gravity_environment: 'earth_1g', mission_day: -1 }],
    ['launch day', TODAY, null, { phase: 'inflight', gravity_environment: 'microgravity', mission_day: 1 }],
    ['inflight', '2026-11-01', null, { phase: 'inflight', gravity_environment: 'microgravity', mission_day: 11 }],
    ['inflight before landing', '2026-11-01', '2026-11-12', { phase: 'inflight', gravity_environment: 'microgravity', mission_day: 11 }],
    ['landing day', '2026-05-01', TODAY, { phase: 'postflight', gravity_environment: 'earth_1g', mission_day: 195 }],
    ['after landing', '2026-05-01', '2026-11-01', { phase: 'postflight', gravity_environment: 'earth_1g', mission_day: 195 }],
  ])('%s', (_label, launch, landing, expected) => {
    expect(deriveDailyContext(profile(launch, landing), TODAY)).toEqual(expected);
  });

  it.each([null, undefined])('is null without a launch date (%s)', (launch) => {
    expect(deriveDailyContext(profile(launch), TODAY)).toBeNull();
  });
});

function build(session: AuthSession | null = UNLOCKED) {
  const backend = new FakeBackend();
  const logged: unknown[] = [];
  const service = createDailyContextService({
    auth: { current: () => session },
    records: backend.records,
    writer: backend.writer,
    now: () => NOW,
    logError: (e) => logged.push(e),
  });
  return { backend, service, logged };
}

const STORED: DailyContext = {
  schema_version: '1.0.0',
  astronaut_id: ID,
  date: TODAY,
  mission_day: 4,
  phase: 'preflight',
  gravity_environment: 'earth_1g',
  medication: [{ name: 'ibuprofen' }],
  exercise: [{ kind: 'treadmill' }],
} as unknown as DailyContext;

describe('createDailyContextService', () => {
  it("reads today's context for the unlocked astronaut, null when none", async () => {
    const { backend, service } = build();
    expect(await service.today()).toBeNull();
    backend.contexts.set(`${ID}/${TODAY}`, STORED);
    expect(await service.today()).toEqual(STORED);
  });

  it('rejects today with locked behind the lock screen or signed out', async () => {
    await expect(build({ crewId: ID, locked: true }).service.today()).rejects.toThrow('locked');
    await expect(build(null).service.today()).rejects.toThrow('locked');
  });

  it('updates today keeping what the UI does not edit, and leaves out an unknown mission day', async () => {
    const { backend, service } = build();
    backend.contexts.set(`${ID}/${TODAY}`, STORED);
    const result = await service.updateToday({ phase: 'inflight', gravity_environment: 'microgravity', mission_day: null });
    expect(result.ok).toBe(true);
    expect(backend.puts).toEqual([
      {
        path: `${ID}/daily-context/${TODAY}`,
        body: {
          astronaut_id: ID,
          date: TODAY,
          phase: 'inflight',
          gravity_environment: 'microgravity',
          medication: [{ name: 'ibuprofen' }],
          exercise: [{ kind: 'treadmill' }],
        },
      },
    ]);
  });

  it('creates today when none is stored', async () => {
    const { backend, service } = build();
    await service.updateToday({ phase: 'inflight', gravity_environment: 'microgravity', mission_day: 3 });
    expect(backend.puts[0]?.body).toEqual({
      astronaut_id: ID,
      date: TODAY,
      phase: 'inflight',
      gravity_environment: 'microgravity',
      mission_day: 3,
    });
  });

  it('is locked without writing when nobody is unlocked', async () => {
    const { backend, service } = build(null);
    expect(await service.updateToday({ phase: 'inflight', gravity_environment: 'microgravity', mission_day: 1 })).toEqual({
      ok: false,
      reason: 'locked',
    });
    expect(backend.puts).toEqual([]);
  });

  it.each([
    [new ViewsError('locked'), 'locked'],
    [new ViewsError('unavailable', { cause: new Error('down') }), 'unavailable'],
  ] as const)('never writes blindly when reading the current one fails (%s)', async (failure, reason) => {
    const { backend, service, logged } = build();
    backend.readFailure = failure;
    expect(await service.updateToday({ phase: 'inflight', gravity_environment: 'microgravity', mission_day: 1 })).toEqual({
      ok: false,
      reason,
    });
    expect(backend.puts).toEqual([]);
    expect(logged).toEqual(reason === 'unavailable' ? [failure] : []);
  });
});

describe('ensureToday', () => {
  it('writes the derived context when today has none and the profile has a launch date', async () => {
    const { backend, service } = build();
    backend.profiles.set(ID, profile('2026-11-01'));
    await service.ensureToday(ID);
    expect(backend.puts).toEqual([
      {
        path: `${ID}/daily-context/${TODAY}`,
        body: { astronaut_id: ID, date: TODAY, phase: 'inflight', gravity_environment: 'microgravity', mission_day: 11 },
        onlyCreate: true,
      },
    ]);
  });

  it('leaves alone a context stored between its read and its write: the 412 is neither logged nor retried', async () => {
    const { backend, service, logged } = build();
    backend.profiles.set(ID, profile('2026-11-01'));
    backend.beforeCreate = () => backend.contexts.set(`${ID}/${TODAY}`, STORED);
    await expect(service.ensureToday(ID)).resolves.toBeUndefined();
    expect(backend.contexts.get(`${ID}/${TODAY}`)).toEqual(STORED);
    expect(backend.puts).toHaveLength(1);
    expect(logged).toEqual([]);
  });

  it('never overwrites an existing context', async () => {
    const { backend, service } = build();
    backend.profiles.set(ID, profile('2026-11-01'));
    backend.contexts.set(`${ID}/${TODAY}`, STORED);
    await service.ensureToday(ID);
    expect(backend.puts).toEqual([]);
  });

  it('does nothing without a profile or without a launch date', async () => {
    const { backend, service } = build();
    await service.ensureToday(ID);
    backend.profiles.set(ID, profile(null));
    await service.ensureToday(ID);
    expect(backend.puts).toEqual([]);
  });

  it('only logs failures, never rejects', async () => {
    const { backend, service, logged } = build();
    backend.readFailure = new ViewsError('unavailable');
    await expect(service.ensureToday(ID)).resolves.toBeUndefined();
    backend.readFailure = null;
    backend.profiles.set(ID, profile('2026-11-01'));
    backend.writeAnswer = { ok: false, reason: 'conflict' };
    await expect(service.ensureToday(ID)).resolves.toBeUndefined();
    expect(logged).toHaveLength(2);
  });
});
