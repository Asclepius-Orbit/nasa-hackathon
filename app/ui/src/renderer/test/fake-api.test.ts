import { describe, expect, it } from 'vitest';

import { FakeAsclepiusApi } from './fake-api';

const verify = (api: FakeAsclepiusApi, pin: string, crewId = 'AST-01'): ReturnType<FakeAsclepiusApi['auth']['verifyPin']> =>
  api.auth.verifyPin({ crewId, pin });

describe('FakeAsclepiusApi honours the AsclepiusApi contract', () => {
  it('counts attempts left down on wrong PINs', async () => {
    const api = new FakeAsclepiusApi();
    expect(await verify(api, '0000')).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
    expect(await verify(api, '0000')).toEqual({ ok: false, reason: 'wrong_pin', attemptsLeft: 1 });
  });

  it('locks after 3 wrong PINs and stays locked even for the correct PIN', async () => {
    const api = new FakeAsclepiusApi();
    await verify(api, '0000');
    await verify(api, '0000');
    const third = await verify(api, '0000');
    expect(third).toMatchObject({ ok: false, reason: 'locked' });
    if (third.ok || third.reason !== 'locked') throw new Error('expected locked');
    expect(third.retryAfterMs).toBeGreaterThan(0);
    expect(await verify(api, '1234')).toMatchObject({ ok: false, reason: 'locked' });
  });

  it('reports unknown crew and signs in on the correct PIN', async () => {
    const api = new FakeAsclepiusApi();
    expect(await verify(api, '1234', 'AST-99')).toEqual({ ok: false, reason: 'unknown_crew' });
    expect(await verify(api, '1234')).toEqual({ ok: true, crewId: 'AST-01' });
    expect(await api.auth.current()).toEqual({ crewId: 'AST-01', locked: false });
  });

  it('lock() keeps the session but marks it locked', async () => {
    const api = new FakeAsclepiusApi();
    await verify(api, '1234');
    await api.auth.lock();
    expect(await api.auth.current()).toEqual({ crewId: 'AST-01', locked: true });
  });

  it('rejects a case-insensitive duplicate name', async () => {
    const api = new FakeAsclepiusApi();
    expect(await api.crew.create({ name: ' elena ruiz ', pin: '1111' })).toEqual({ ok: false, reason: 'name_taken' });
  });

  it('rejects a name shorter than 2 characters after trimming', async () => {
    const api = new FakeAsclepiusApi();
    expect(await api.crew.create({ name: ' A ', pin: '1111' })).toEqual({ ok: false, reason: 'name_too_short' });
  });

  it('nextId increments after a create, and the new member is signed in', async () => {
    const api = new FakeAsclepiusApi();
    expect(await api.crew.nextId()).toBe('AST-03');
    const result = await api.crew.create({ name: 'Ana Sol', pin: '1111' });
    expect(result).toMatchObject({ ok: true, member: { id: 'AST-03', name: 'Ana Sol' } });
    expect(await api.crew.nextId()).toBe('AST-04');
    expect(await api.auth.current()).toEqual({ crewId: 'AST-03', locked: false });
  });

  it('defaults to the large text size and merges settings patches', async () => {
    const api = new FakeAsclepiusApi();
    expect((await api.settings.get()).textSize).toBe('large');
    expect(await api.settings.update({ footPedal: true })).toMatchObject({ footPedal: true, textSize: 'large' });
  });
});
