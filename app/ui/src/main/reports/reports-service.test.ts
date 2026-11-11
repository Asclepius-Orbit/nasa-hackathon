import { describe, expect, it } from 'vitest';

import type { AuthSession, DailyReportFields } from '../../shared/api';
import type { ExerciseEntry } from '../profile/daily-context-service';
import { FakeBackend } from '../profile/test-fakes';
import { ViewsError } from '../views/views-error';
import { createReportsService } from './reports-service';

const ID = 'AST-01';
const UNLOCKED: AuthSession = { crewId: ID, locked: false };
const REPORT = '5e1f0e90-0000-4000-8000-000000000001';

const FIELDS: DailyReportFields = {
  pain_nrs: 5,
  stiffness_nrs: 6,
  pain_locations: [
    { region: 'lumbar', side: 'bilateral' },
    { region: 'thoracic', side: null },
  ],
  sleep_hours: 5.5,
  sleep_quality_nrs: 4,
  red_flags: ['numbness_or_tingling'],
  exercise: null,
};

const saved = (assessment: unknown = { level: 'amber' }, skipped: string | null = null) => ({
  report: { report_id: REPORT, kind: 'daily' },
  assessment,
  assessment_skipped: skipped,
});

function build(session: AuthSession | null = UNLOCKED) {
  const backend = new FakeBackend();
  const exercises: [string, ExerciseEntry][] = [];
  let next = 0;
  const service = createReportsService({
    auth: { current: () => session },
    records: backend.records,
    writer: backend.writer,
    dailyContext: {
      addExercise: (id, entry) => {
        exercises.push([id, entry]);
        return Promise.resolve();
      },
    },
    newId: () => `00000000-0000-4000-8000-00000000000${String(++next)}`,
  });
  backend.sendAnswer = saved();
  return { backend, service, exercises };
}

describe('createReportsService saveDaily', () => {
  it('POSTs the daily report with a fresh id and answers the assessment it led to', async () => {
    const { backend, service } = build();
    expect(await service.saveDaily(FIELDS)).toEqual({
      ok: true,
      value: { reportId: REPORT, assessmentLevel: 'amber', assessmentSkipped: null },
    });
    expect(backend.sent).toEqual([
      {
        method: 'POST',
        path: `/ui/crew/${ID}/self-reports`,
        astronautId: ID,
        body: {
          report_id: '00000000-0000-4000-8000-000000000001',
          kind: 'daily',
          pain_nrs: 5,
          stiffness_nrs: 6,
          pain_locations: [{ region: 'lumbar', side: 'bilateral' }, { region: 'thoracic' }],
          sleep: { hours: 5.5, quality_nrs: 4 },
          red_flags: ['numbness_or_tingling'],
        },
      },
    ]);
  });

  it('leaves out empty locations and an unknown sleep, but always answers the red flags (empty: asked, none)', async () => {
    const { backend, service } = build();
    await service.saveDaily({ ...FIELDS, pain_locations: [], sleep_hours: null, sleep_quality_nrs: 3, red_flags: [] });
    expect(backend.sent[0]?.body).toEqual({
      report_id: '00000000-0000-4000-8000-000000000001',
      kind: 'daily',
      pain_nrs: 5,
      stiffness_nrs: 6,
      red_flags: [],
    });
  });

  it('sends the sleep hours without a quality when none was given', async () => {
    const { backend, service } = build();
    await service.saveDaily({ ...FIELDS, sleep_quality_nrs: null });
    expect(backend.sent[0]?.body).toMatchObject({ sleep: { hours: 5.5 } });
    expect(backend.sent[0]?.body).not.toHaveProperty('sleep.quality_nrs');
  });

  it('answers a skipped assessment with its reason', async () => {
    const { backend, service } = build();
    backend.sendAnswer = saved(null, 'no_profile');
    expect(await service.saveDaily(FIELDS)).toEqual({
      ok: true,
      value: { reportId: REPORT, assessmentLevel: null, assessmentSkipped: 'no_profile' },
    });
  });

  it("adds the exercise to today's daily context after the report is stored", async () => {
    const { service, exercises } = build();
    await service.saveDaily({ ...FIELDS, exercise: { modality: 'ared', duration_min: 45 } });
    expect(exercises).toEqual([[ID, { modality: 'ared', duration_min: 45 }]]);
  });

  it('adds no exercise when there is none or the report was refused', async () => {
    const { backend, service, exercises } = build();
    await service.saveDaily(FIELDS);
    backend.writeAnswer = { ok: false, reason: 'invalid', fields: [] };
    expect(await service.saveDaily({ ...FIELDS, exercise: { modality: 'ared', duration_min: 45 } })).toEqual(backend.writeAnswer);
    expect(exercises).toEqual([]);
  });

  it('keeps the report id for a retry of the same report after an unavailable answer, and only then', async () => {
    const { backend, service } = build();
    backend.writeAnswer = { ok: false, reason: 'unavailable' };
    await service.saveDaily(FIELDS);
    await service.saveDaily(FIELDS);
    backend.writeAnswer = null;
    await service.saveDaily(FIELDS);
    await service.saveDaily(FIELDS);
    backend.writeAnswer = { ok: false, reason: 'unavailable' };
    await service.saveDaily(FIELDS);
    await service.saveDaily({ ...FIELDS, pain_nrs: 6 });
    expect(backend.sent.map((r) => (r.body as { report_id: string }).report_id.slice(-1))).toEqual(['1', '1', '1', '2', '3', '4']);
  });

  it('refuses an answer without the report id', async () => {
    const { backend, service } = build();
    backend.sendAnswer = { report: {}, assessment: null, assessment_skipped: null };
    await expect(service.saveDaily(FIELDS)).rejects.toThrow(/outside the schema/);
  });
});

describe('createReportsService saveOswestry and due', () => {
  it('POSTs the weekly ODI items', async () => {
    const { backend, service } = build();
    const items = [
      { item_id: 'pain_intensity', score: 2 },
      { item_id: 'lifting', score: 3 },
    ];
    expect(await service.saveOswestry({ items })).toMatchObject({ ok: true, value: { reportId: REPORT } });
    expect(backend.sent[0]).toEqual({
      method: 'POST',
      path: `/ui/crew/${ID}/self-reports`,
      astronautId: ID,
      body: { report_id: '00000000-0000-4000-8000-000000000001', kind: 'weekly_odi', items },
    });
  });

  it('reads what is due for the unlocked astronaut', async () => {
    const { backend, service } = build();
    expect(await service.due()).toEqual(backend.due);
  });

  it.each([null, { crewId: ID, locked: true }])('is locked when nobody is unlocked (%j)', async (session) => {
    const { backend, service } = build(session);
    expect(await service.saveDaily(FIELDS)).toEqual({ ok: false, reason: 'locked' });
    expect(await service.saveOswestry({ items: [{ item_id: 'lifting', score: 1 }] })).toEqual({ ok: false, reason: 'locked' });
    await expect(service.due()).rejects.toEqual(new ViewsError('locked'));
    expect(backend.sent).toEqual([]);
  });
});
