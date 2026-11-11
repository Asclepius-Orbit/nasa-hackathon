import { describe, expect, it } from 'vitest';

import type { AuthSession, CrewMember } from '../../shared/api';
import { FakeBackend } from '../profile/test-fakes';
import { createUrgentService } from './urgent-service';

const REPORT = '5e1f0e90-0000-4000-8000-000000000003';
const NOW = Date.UTC(2026, 10, 11, 14, 12);
const CREW: CrewMember[] = [
  { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-01' },
  { id: 'AST-02', name: 'Marc Dubois', harnessId: null },
];
const INPUT = { crewId: 'AST-02', red_flags: ['leg_weakness'], other_symptoms: ['very_severe_pain'] };

function build(session: AuthSession | null, crew: CrewMember[] | null = CREW) {
  const backend = new FakeBackend();
  backend.sendAnswer = { report: { report_id: REPORT }, assessment: { level: 'red' }, assessment_skipped: null };
  const service = createUrgentService({
    auth: { current: () => session },
    crew: { list: () => Promise.resolve(crew) },
    writer: backend.writer,
    now: () => NOW,
    newId: () => REPORT,
  });
  return { backend, service };
}

const expectedRequest = (crewId: string, body: Record<string, unknown>) => ({
  method: 'POST',
  path: `/ui/crew/${crewId}/self-reports`,
  astronautId: crewId,
  body: { report_id: REPORT, kind: 'red_flag', ...body, onset: '2026-11-11T14:12:00.000Z' },
});

describe('createUrgentService', () => {
  it('sends the red-flag report of the unlocked member', async () => {
    const { backend, service } = build({ crewId: 'AST-02', locked: false });
    expect(await service.send(INPUT)).toEqual({
      ok: true,
      value: { reportId: REPORT, assessmentLevel: 'red', assessmentSkipped: null },
    });
    expect(backend.sent).toEqual([
      expectedRequest('AST-02', { red_flags: ['leg_weakness'], other_symptoms: ['very_severe_pain'] }),
    ]);
  });

  it('refuses a report for someone else while a member is unlocked', async () => {
    const { backend, service } = build({ crewId: 'AST-01', locked: false });
    expect(await service.send(INPUT)).toEqual({ ok: false, reason: 'locked' });
    expect(backend.sent).toEqual([]);
  });

  it.each([null, { crewId: 'AST-01', locked: true }])(
    'sends without a PIN for the chosen crew member when nobody is unlocked (%j), leaving out no other symptoms',
    async (session) => {
      const { backend, service } = build(session);
      await service.send({ ...INPUT, other_symptoms: [] });
      expect(backend.sent).toEqual([expectedRequest('AST-02', { red_flags: ['leg_weakness'] })]);
    },
  );

  it('refuses an id that is not in the crew when nobody is unlocked', async () => {
    const { backend, service } = build(null);
    expect(await service.send({ ...INPUT, crewId: 'AST-09' })).toEqual({ ok: false, reason: 'locked' });
    expect(backend.sent).toEqual([]);
  });

  it('is unavailable when the crew cannot be read to check the id', async () => {
    const { backend, service } = build(null, null);
    expect(await service.send(INPUT)).toEqual({ ok: false, reason: 'unavailable' });
    expect(backend.sent).toEqual([]);
  });

  it('resends the same report (id and onset) when the first try was unavailable', async () => {
    const backend = new FakeBackend();
    let now = NOW;
    let next = 0;
    const service = createUrgentService({
      auth: { current: () => ({ crewId: 'AST-02', locked: false }) },
      crew: { list: () => Promise.resolve(CREW) },
      writer: backend.writer,
      now: () => now,
      newId: () => String(++next),
    });
    backend.writeAnswer = { ok: false, reason: 'unavailable' };
    await service.send(INPUT);
    now += 60_000;
    await service.send(INPUT);
    expect(backend.sent[1]?.body).toEqual(backend.sent[0]?.body);
  });
});
