import { expect, it } from 'vitest';

import type { AstronautProfile, AuthSession } from '../../shared/api';
import { createDailyContextRefresh } from './daily-context-refresh';
import { createDailyContextService } from './daily-context-service';
import { FakeBackend } from './test-fakes';

it('creates the new UTC day for a session that remains unlocked and preserves an existing context', async () => {
  const backend = new FakeBackend();
  const session: AuthSession = { crewId: 'AST-01', locked: false };
  let now = Date.UTC(2026, 10, 11, 23, 59);
  backend.profiles.set(session.crewId, { astronaut_id: session.crewId, launch_date: '2026-11-11' } as AstronautProfile);
  const auth = { current: () => session };
  const daily = createDailyContextService({ auth, records: backend.records, writer: backend.writer, now: () => now });
  const refresh = createDailyContextRefresh(auth, daily);
  await refresh();
  now = Date.UTC(2026, 10, 12);
  await refresh();
  await refresh();
  expect(backend.puts.map((put) => put.path)).toEqual(['AST-01/daily-context/2026-11-11', 'AST-01/daily-context/2026-11-12']);
  expect(backend.contexts.get('AST-01/2026-11-12')?.mission_day).toBe(2);
  session.locked = true;
  now = Date.UTC(2026, 10, 13);
  await refresh();
  expect(backend.puts).toHaveLength(2);
});
