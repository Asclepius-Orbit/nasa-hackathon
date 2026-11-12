import { describe, expect, it } from 'vitest';

import type { AuthSession } from '../../shared/api';
import type { CrewStore } from '../store/crew-store';
import { createHarnessService } from './harness-service';

const MEMBER = { id: 'AST-01', name: 'Elena Ruiz', harnessId: null };

function build(session: AuthSession | null, answer: Awaited<ReturnType<CrewStore['setHarness']>>) {
  const calls: [string, string | null][] = [];
  const service = createHarnessService({
    auth: { current: () => session },
    crew: {
      setHarness: (id, harnessId) => {
        calls.push([id, harnessId]);
        return Promise.resolve(answer);
      },
    },
  });
  return { service, calls };
}

describe('createHarnessService', () => {
  it('pairs the unlocked member only', async () => {
    const answer = { ok: true, member: { ...MEMBER, harnessId: 'wb-07' } } as const;
    const { service, calls } = build({ crewId: 'AST-01', locked: false }, answer);
    expect(await service.setHarness({ harnessId: 'wb-07' })).toEqual(answer);
    expect(calls).toEqual([['AST-01', 'wb-07']]);
  });

  it.each([null, { crewId: 'AST-01', locked: true }])('is locked without touching the store (%j)', async (session) => {
    const { service, calls } = build(session, { ok: true, member: MEMBER });
    expect(await service.setHarness({ harnessId: null })).toEqual({ ok: false, reason: 'locked' });
    expect(calls).toEqual([]);
  });

  it.each(['unknown', 'unavailable'] as const)('turns a store %s into unavailable', async (reason) => {
    const { service } = build({ crewId: 'AST-01', locked: false }, { ok: false, reason });
    expect(await service.setHarness({ harnessId: null })).toEqual({ ok: false, reason: 'unavailable' });
  });
});
