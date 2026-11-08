import { describe, expect, it } from 'vitest';

import type { AuthSession, CrewMember } from '../../shared/api';
import type { ViewsClient } from './views-client';
import { ViewsError } from './views-error';
import { createViewsService } from './views-service';

const SESSION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const MEMBERS: CrewMember[] = [
  { id: 'AST-01', name: 'Elena María Ruiz', harnessId: 'wb-01' },
  { id: 'AST-02', name: '  Marc   Dubois', harnessId: null },
];

/** Records which astronaut each call was made for; answers with a marker so tests can tell views apart. */
function fakeClient(): { client: ViewsClient; calls: string[] } {
  const calls: string[] = [];
  const view = (name: string, ...args: unknown[]): Promise<never> => {
    calls.push(`${name}:${args.map((a) => JSON.stringify(a)).join(',')}`);
    return Promise.resolve({ name } as never);
  };
  const client: ViewsClient = {
    strip: (id) => view('strip', id),
    today: (id) => view('today', id),
    history: (id, range) => view('history', id, range),
    baseline: (id) => view('baseline', id),
    firstDay: (id) => view('first-day', id),
    session: (astronautId, id) => view('session', astronautId, id),
  };
  return { client, calls };
}

function build(session: AuthSession | null, members: CrewMember[] | null = MEMBERS) {
  const { client, calls } = fakeClient();
  const service = createViewsService({
    auth: { current: () => session },
    crew: { list: () => Promise.resolve(members) },
    client,
  });
  return { service, calls };
}

const UNLOCKED: AuthSession = { crewId: 'AST-01', locked: false };

async function codeOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ViewsError) return error.code;
    throw error;
  }
  throw new Error('expected a ViewsError');
}

describe('createViewsService', () => {
  it('asks the backend for the unlocked astronaut, never one chosen by the caller', async () => {
    const { service, calls } = build(UNLOCKED);
    await service.strip();
    await service.today();
    await service.history({ from: '2026-11-01' });
    await service.baseline();
    await service.session(SESSION);
    expect(calls).toEqual([
      'strip:"AST-01"',
      'today:"AST-01"',
      'history:"AST-01",{"from":"2026-11-01"}',
      'baseline:"AST-01"',
      `session:"AST-01","${SESSION}"`,
    ]);
  });

  it('follows whoever is signed in', async () => {
    const { service, calls } = build({ crewId: 'AST-02', locked: false });
    await service.strip();
    expect(calls).toEqual(['strip:"AST-02"']);
  });

  it.each<[string, AuthSession | null]>([
    ['nobody is signed in', null],
    ['the lock screen is up', { crewId: 'AST-01', locked: true }],
  ])('fails with "locked" and sends no request when %s', async (_label, session) => {
    const { service, calls } = build(session);
    const attempts = [
      service.strip(),
      service.today(),
      service.history({}),
      service.baseline(),
      service.firstDay(),
      service.session(SESSION),
    ];
    for (const attempt of attempts) expect(await codeOf(attempt)).toBe('locked');
    expect(calls).toEqual([]);
  });

  describe('firstDay', () => {
    it('adds the local first name to the backend view', async () => {
      const { service } = build(UNLOCKED);
      expect(await service.firstDay()).toEqual({ name: 'first-day', crewFirstName: 'Elena' });
    });

    it('ignores whitespace around the name', async () => {
      const { service } = build({ crewId: 'AST-02', locked: false });
      expect((await service.firstDay()).crewFirstName).toBe('Marc');
    });

    it('is unavailable when the crew store cannot be read', async () => {
      const { service } = build(UNLOCKED, null);
      expect(await codeOf(service.firstDay())).toBe('unavailable');
    });

    it('is unavailable when the signed-in member is not in the store', async () => {
      const { service } = build({ crewId: 'AST-09', locked: false });
      expect(await codeOf(service.firstDay())).toBe('unavailable');
    });
  });
});
