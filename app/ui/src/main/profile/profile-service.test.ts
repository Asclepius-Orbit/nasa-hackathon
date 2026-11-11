import { describe, expect, it } from 'vitest';

import type { AstronautProfile, AuthSession, ProfileFields } from '../../shared/api';
import { ViewsError } from '../views/views-error';
import { createDevicesService } from './devices-service';
import { createProfileService } from './profile-service';
import { FakeBackend } from './test-fakes';

const ID = 'AST-01';
const UNLOCKED: AuthSession = { crewId: ID, locked: false };

const FIELDS: ProfileFields = {
  date_of_birth: '1985-04-02',
  sex_at_birth: 'female',
  launch_date: '2026-11-01',
  landing_date: null,
  height_standing_cm: 171,
  height_sitting_cm: null,
  body_mass_kg: 61.5,
};

const STORED = {
  schema_version: '1.0.0',
  astronaut_id: ID,
  updated_at: '2026-10-01T00:00:00+00:00',
  date_of_birth: '1984-01-01',
  sex_at_birth: 'undisclosed',
  gender_identity: 'woman',
  launch_date: '2026-12-01',
  landing_date: '2027-06-01',
  anthropometry: { height_standing_cm: 170, height_sitting_cm: 90, body_mass_kg: 62, arm_span_cm: 168 },
  spine_history: { prior_back_pain: true },
} as unknown as AstronautProfile;

function build(session: AuthSession | null = UNLOCKED) {
  const backend = new FakeBackend();
  const ensured: string[] = [];
  const logged: unknown[] = [];
  const service = createProfileService({
    auth: { current: () => session },
    records: backend.records,
    writer: backend.writer,
    dailyContext: {
      ensureToday: (id) => {
        ensured.push(id);
        return Promise.resolve();
      },
    },
    logError: (e) => logged.push(e),
  });
  return { backend, service, ensured, logged };
}

describe('createProfileService', () => {
  it('reads the unlocked astronaut profile, null when none', async () => {
    const { backend, service } = build();
    expect(await service.get()).toBeNull();
    backend.profiles.set(ID, STORED);
    expect(await service.get()).toEqual(STORED);
  });

  it('rejects get with locked when nobody is unlocked', async () => {
    await expect(build(null).service.get()).rejects.toThrow('locked');
    await expect(build({ crewId: ID, locked: true }).service.get()).rejects.toThrow('locked');
  });

  it('keeps every stored field the UI does not edit and lets the backend set version and time', async () => {
    const { backend, service, ensured } = build();
    backend.profiles.set(ID, STORED);
    const result = await service.update(FIELDS);
    expect(result.ok).toBe(true);
    expect(backend.puts).toEqual([
      {
        path: `${ID}/profile`,
        body: {
          astronaut_id: ID,
          date_of_birth: '1985-04-02',
          sex_at_birth: 'female',
          gender_identity: 'woman',
          launch_date: '2026-11-01',
          landing_date: null,
          anthropometry: { height_standing_cm: 171, body_mass_kg: 61.5, arm_span_cm: 168 },
          spine_history: { prior_back_pain: true },
        },
      },
    ]);
    expect(ensured).toEqual([ID]);
  });

  it('creates the first profile with the sitting height when given', async () => {
    const { backend, service } = build();
    await service.update({ ...FIELDS, height_sitting_cm: 88 });
    expect(backend.puts[0]?.body).toEqual({
      astronaut_id: ID,
      date_of_birth: '1985-04-02',
      sex_at_birth: 'female',
      launch_date: '2026-11-01',
      landing_date: null,
      anthropometry: { height_standing_cm: 171, height_sitting_cm: 88, body_mass_kg: 61.5 },
    });
  });

  it('is locked without reading or writing when nobody is unlocked', async () => {
    const { backend, service } = build({ crewId: ID, locked: true });
    expect(await service.update(FIELDS)).toEqual({ ok: false, reason: 'locked' });
    expect(backend.puts).toEqual([]);
  });

  it('never overwrites blindly when the stored profile cannot be read', async () => {
    const { backend, service, logged, ensured } = build();
    const failure = new ViewsError('unavailable');
    backend.readFailure = failure;
    expect(await service.update(FIELDS)).toEqual({ ok: false, reason: 'unavailable' });
    expect(backend.puts).toEqual([]);
    expect(logged).toEqual([failure]);
    expect(ensured).toEqual([]);
  });

  it('passes a refused write through and does not derive the daily context', async () => {
    const { backend, service, ensured } = build();
    backend.writeAnswer = { ok: false, reason: 'invalid', fields: [{ pointer: '/date_of_birth', message: 'x' }] };
    expect(await service.update(FIELDS)).toEqual(backend.writeAnswer);
    expect(ensured).toEqual([]);
  });
});

describe('createDevicesService', () => {
  it('lists the devices of the unlocked astronaut', async () => {
    const backend = new FakeBackend();
    backend.devices = [{ device_id: 'wb-01' } as never];
    const service = createDevicesService({ auth: { current: () => UNLOCKED }, records: backend.records });
    expect(await service.list()).toEqual([{ device_id: 'wb-01' }]);
  });

  it('rejects with locked when nobody is unlocked', async () => {
    const service = createDevicesService({ auth: { current: () => null }, records: new FakeBackend().records });
    await expect(service.list()).rejects.toThrow('locked');
  });
});
