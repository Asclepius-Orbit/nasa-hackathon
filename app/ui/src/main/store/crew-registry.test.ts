import { describe, expect, it } from 'vitest';

import type { CrewMember } from '../../shared/api';
import { createCrewRegistry, type RegisterFetch, withCrewRegistry } from './crew-registry';
import type { CrewStore } from './crew-store';

const BASE = 'http://127.0.0.1:8000';
const MEMBER: CrewMember = { id: 'AST-03', name: 'Ana', harnessId: null };

function fakeStore(members: CrewMember[]): CrewStore {
  return {
    list: () => Promise.resolve(members),
    nextId: () => Promise.resolve('AST-04'),
    create: () => Promise.resolve({ ok: true, member: MEMBER }),
    findCredentials: () => Promise.resolve({ status: 'unknown' }),
    setHarness: (id, harnessId) => Promise.resolve({ ok: true, member: { ...MEMBER, id, harnessId } }),
  };
}

function backend(up: () => boolean) {
  const calls: { url: string; init: Parameters<RegisterFetch>[1] }[] = [];
  const fetch: RegisterFetch = (url, init) => {
    calls.push({ url, init });
    return up() ? Promise.resolve({ ok: true }) : Promise.reject(new Error('ECONNREFUSED'));
  };
  return { calls, fetch };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('crew registry', () => {
  it('registers a new member by id only: no body, so neither the name nor the PIN leaves the main process', async () => {
    const { calls, fetch } = backend(() => true);
    const crew = withCrewRegistry(fakeStore([]), createCrewRegistry({ baseUrl: BASE, fetch }));

    const result = await crew.create({ name: 'Ana', pin: '1234' });
    await settle();

    expect(result).toEqual({ ok: true, member: MEMBER });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`${BASE}/ui/crew/AST-03`);
    expect(calls[0]?.init).not.toHaveProperty('body');
  });

  it('keeps the member when the backend is down and registers it on a later list, once', async () => {
    let up = false;
    const errors: string[] = [];
    const { calls, fetch } = backend(() => up);
    const crew = withCrewRegistry(fakeStore([MEMBER]), createCrewRegistry({ baseUrl: BASE, fetch, logError: (m) => errors.push(m) }));

    expect((await crew.create({ name: 'Ana', pin: '1234' })).ok).toBe(true);
    await settle();
    expect(errors).toEqual(['crew registry: AST-03 not registered (backend unavailable)']);

    up = true;
    await crew.list();
    await settle();
    await crew.list();
    await settle();

    expect(calls.map((c) => c.url)).toEqual([`${BASE}/ui/crew/AST-03`, `${BASE}/ui/crew/AST-03`]);
  });

  it('passes harness pairing through to the store, without contacting the backend', async () => {
    const { calls, fetch } = backend(() => true);
    const crew = withCrewRegistry(fakeStore([]), createCrewRegistry({ baseUrl: BASE, fetch }));
    expect(await crew.setHarness('AST-03', 'wb-07')).toEqual({ ok: true, member: { ...MEMBER, harnessId: 'wb-07' } });
    await settle();
    expect(calls).toEqual([]);
  });
});
