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
    // The backend answers with the latest device to report when no device_id is asked for.
    strip: async (id, deviceId) => {
      await view('strip', id, ...(deviceId === undefined ? [] : [deviceId]));
      return { name: 'strip', harness: { device_id: 'latest-01' } } as never;
    },
    today: (id) => view('today', id),
    history: (id, range) => view('history', id, range),
    baseline: (id) => view('baseline', id),
    firstDay: (id) => view('first-day', id),
    cordProtocols: (id) => view('cord-protocols', id),
    cordHistory: (id) => view('cord-history', id),
    session: (astronautId, id) => view('session', astronautId, id),
    houston: (id) => view('houston', id),
    outbox: (id) => view('outbox', id),
    houstonReport: async (id) => {
      await view('houston-report', id);
      return { fileName: 'houston-report.pdf', bytes: new Uint8Array() };
    },
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
    await service.houston();
    await service.outbox();
    await service.houstonReport();
    expect(calls).toEqual([
      'strip:"AST-01","wb-01"',
      'today:"AST-01"',
      'history:"AST-01",{"from":"2026-11-01"}',
      'baseline:"AST-01"',
      `session:"AST-01","${SESSION}"`,
      'houston:"AST-01"',
      'outbox:"AST-01"',
      'houston-report:"AST-01"',
    ]);
  });

  it('follows whoever is signed in', async () => {
    const { service, calls } = build({ crewId: 'AST-02', locked: false });
    await service.strip();
    expect(calls).toEqual(['strip:"AST-02"']);
  });

  describe('strip', () => {
    it('asks for the paired harness of the unlocked member, not for the latest device to report', async () => {
      const { service, calls } = build(UNLOCKED);
      expect(await service.strip()).toEqual({ name: 'strip', harness: { device_id: 'latest-01' } });
      expect(calls).toEqual(['strip:"AST-01","wb-01"']);
    });

    it.each<[string, AuthSession, CrewMember[] | null]>([
      ['has no harness paired', { crewId: 'AST-02', locked: false }, MEMBERS],
      ['is not in the crew store', { crewId: 'AST-09', locked: false }, MEMBERS],
      ['cannot be looked up because the crew store is unreadable', UNLOCKED, null],
    ])('shows no harness when the member %s, whatever device reported last', async (_label, session, members) => {
      const { service, calls } = build(session, members);
      expect(await service.strip()).toEqual({ name: 'strip', harness: null });
      expect(calls).toEqual([`strip:"${session.crewId}"`]);
    });
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
      service.cordProtocols(),
      service.cordHistory(),
      service.session(SESSION),
      service.houston(),
      service.outbox(),
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
