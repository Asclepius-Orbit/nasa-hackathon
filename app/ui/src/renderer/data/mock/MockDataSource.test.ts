import { beforeEach, describe, expect, it } from 'vitest';

import { FakeScheduler } from '../../test/fake-scheduler';
import type { DataSource } from '../DataSource';
import type { RedFlagCode, RepState } from '../types';
import { MockDataSource } from './MockDataSource';

const NOW = new Date('2026-11-05T13:40:00Z');

let scheduler: FakeScheduler;
let data: MockDataSource;
beforeEach(() => {
  scheduler = new FakeScheduler();
  data = new MockDataSource({ now: () => NOW, scheduler });
});

describe('catalogue', () => {
  it('lists the 8 contract red flags, most serious first, plus 2 escapes outside the contract', async () => {
    const { flags, escapes } = await data.getCatalog();
    const expected: RedFlagCode[] = [
      'saddle_numbness',
      'bladder_or_bowel_dysfunction',
      'leg_weakness',
      'radiating_leg_pain',
      'numbness_or_tingling',
      'severe_night_pain',
      'fever_with_back_pain',
      'trauma_event',
    ];
    expect(flags.map((f) => f.code)).toEqual(expected);
    expect(flags[0]?.label).toBe('Numbness between the legs or around the bottom');
    expect(escapes.map((e) => e.code)).toEqual(['very_severe_pain', 'other']);
  });
});

describe('strip', () => {
  it('has the mock values and a mission start that makes today day 87', async () => {
    const strip = await data.getStrip();
    expect(strip).toMatchObject({
      missionDay: 87,
      flightPhase: 'inflight',
      harness: { id: 'wb-01', batteryPct: 64, weakStreams: [{ stream: 'emg_lat_right', state: 'stalled' }], degraded: false, stale: false, lastSeenAt: '2026-11-05T13:39:00.000Z' },
      groundLink: { state: 'down', backInMin: 12 },
      lastContactMinAgo: 4,
      reportsDue: 1,
    });
    expect(strip.missionStart).toBe('2026-08-11');
  });
});

describe('history', () => {
  it('spans mission days 58 to 87 in order', async () => {
    const { days, todayDay } = await data.getHistory();
    expect(days).toHaveLength(30);
    expect(days[0]?.day).toBe(58);
    expect(days.at(-1)?.day).toBe(87);
    expect(todayDay).toBe(87);
  });

  it('is green before day 80, no data on 80 to 82, amber from day 83', async () => {
    const { days } = await data.getHistory();
    const counts = { green: 0, amber: 0, red: 0, nodata: 0 };
    for (const d of days) counts[d.level] += 1;
    expect(counts).toEqual({ green: 22, amber: 5, red: 0, nodata: 3 });
    expect(days.find((d) => d.day === 79)?.level).toBe('green');
    expect(days.find((d) => d.day === 83)?.level).toBe('amber');
  });

  it('keeps absence explicit: no-data days have null values, never zero', async () => {
    const { days } = await data.getHistory();
    const empty = days.filter((d) => d.level === 'nodata');
    expect(empty.map((d) => d.day)).toEqual([80, 81, 82]);
    for (const d of empty) {
      expect(d.lengthMm).toBeNull();
      expect(d.pain).toBeNull();
      expect(d.report).toBeNull();
      expect(d.session).toBeNull();
    }
  });

  it('generates the mock values deterministically', async () => {
    const { days } = await data.getHistory();
    const again = await new MockDataSource({ now: () => NOW, scheduler }).getHistory();
    expect(again).toEqual({ ...again, days });
    expect(days.find((d) => d.day === 58)?.lengthMm).toBeCloseTo(-0.4 + ((58 * 37) % 9) / 10, 10);
    expect(days.find((d) => d.day === 83)?.lengthMm).toBeCloseTo(1.2, 10);
    expect(days.find((d) => d.day === 87)?.lengthMm).toBeCloseTo(2.08, 10);
    expect(days.find((d) => d.day === 71)?.pain).toBe(2);
    expect(days.find((d) => d.day === 79)?.pain).toBe(3);
    expect(days.find((d) => d.day === 87)?.pain).toBe(4);
    expect(days.find((d) => d.day === 59)?.session).toMatchObject({ protocolId: 'lat_pulldown_standing_v1', state: 'processed' });
    expect(days.find((d) => d.day === 60)?.session).toMatchObject({ protocolId: 'spine_length_rest_v1' });
    expect(days.find((d) => d.day === 87)).toMatchObject({ date: '2026-11-05', report: { kind: 'daily', redFlags: [] } });
    expect(days.find((d) => d.day === 58)?.date).toBe('2026-10-07');
  });
});

describe('today, report, session, baseline, oswestry, first day', () => {
  it('gives the amber verdict with its plan, reasons and trust numbers', async () => {
    const today = await data.getToday();
    expect(today.level).toBe('amber');
    expect(today.recommendations).toEqual(['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01']);
    expect(today.reasons.map((r) => r.featureId)).toEqual([
      'stretch.lumbar.peak_elongation_mm',
      'imu.l3.sagittal.rom_deg',
      'symptoms.pain_nrs_7d_mean',
    ]);
    expect(today.trust).toMatchObject({ confidencePct: 70, dataAvailable: 8, dataTotal: 10, assessedAt: '2026-11-05T12:58:00.000Z' });
    expect(today.lumbarLengthMm).toBe(2.1);
    expect(today.lumbarMeasuredAt).toBe('2026-11-05T13:20:00.000Z');
    expect(today.sessionInProgress).toMatchObject({ protocolId: 'lat_pulldown_standing_v1', startedAt: '2026-11-05T13:38:00.000Z', totalReps: 10 });
    expect(today.alsoToday).toEqual(['TASK_DAILY_REPORT', 'TASK_ODI']);
  });

  it('gives the session protocol', async () => {
    const s = await data.getSession();
    expect(s).toMatchObject({
      protocolId: 'lat_pulldown_standing_v1',
      band: { color: 'blue', nominalKg: 15 },
      stretchedCm: null,
      totalReps: 10,
      plannedAt: null,
    });
    expect(s?.steps.map((x) => x.status)).toEqual(['done', 'done', 'done', 'now', 'todo']);
    expect(s?.sensorChecks.find((c) => c.status === 'weak')?.stream).toBe('emg_lat_right');
  });

  it('gives the baseline, 2 of 3', async () => {
    const b = await data.getBaseline();
    expect(b).toMatchObject({ recorded: 2, required: 3, referenceVersion: 2, builtFromSessions: 2 });
    expect(b.sessions.map((s) => s.offsetDays)).toEqual([-30, -23, -16]);
    expect(b.sessions.map((s) => s.status)).toEqual(['complete', 'complete', 'planned']);
  });

  it('gives six Oswestry items of six statements each', async () => {
    const { items } = await data.getOswestry();
    expect(items).toHaveLength(6);
    for (const item of items) expect(item.options).toHaveLength(6);
    expect(items[0]?.title).toBe('Pain intensity');
  });

  it('gives the report form choices mapped to contract values', async () => {
    const form = await data.getReportForm();
    expect(form.painRegions.map((r) => r.value)).toEqual(['cervical', 'thoracic', 'lumbar', 'sacral']);
    expect(form.painSides.map((r) => r.value)).toEqual(['left', 'midline', 'right', 'bilateral']);
    expect(form.sleepHours.at(-1)).toEqual({ value: null, label: 'Not sure' });
    expect(form.exerciseKinds.map((k) => k.label)).toEqual(['ARED', 'CEVIS', 'T2 treadmill', 'None yet']);
    expect(form.catalog.flags).toHaveLength(8);
  });

  it('gives the first-day checklist', async () => {
    const f = await data.getFirstDay();
    expect(f.crewFirstName).toBe('Elena');
    expect(f.todo).toEqual(['TASK_BASELINE_SESSION', 'TASK_DAILY_REPORT']);
    expect(f.baseline.recorded).toBe(2);
  });
});

describe('houston and stop', () => {
  it('summarises day 87 with the crew code, never the name', async () => {
    const h = await data.getHouston();
    expect(h).toMatchObject({ crewCode: 'AST-01', transport: 'simulated', contentValidated: false, groundLink: { state: 'down', backInMin: 12 } });
    expect(h.summary).toMatchObject({
      missionDay: 87,
      level: 'amber',
      confidencePct: 70,
      dataAvailable: 8,
      dataTotal: 10,
      redFlags: [],
      sessions: [{ state: 'running' }],
      recommendations: ['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01'],
      contacts: null,
    });
    expect(JSON.stringify(h)).not.toContain('Elena');
    expect(h.outbox.map((o) => [o.missionDay, o.revision, o.state])).toEqual([
      [87, 1, 'queued'],
      [86, 1, 'sent'],
      [85, 1, 'sent'],
    ]);
    expect(h.outbox[1]?.receivedAt).toMatch(/T21:04:00/);
  });

  it('records the stop at the current rep and the officer call', async () => {
    const entry = await data.logCrewMedicalOfficerCall();
    expect(entry.atGmt).toBe('13:52');
    expect(entry.text).toBe('13:52 GMT: call to the crew medical officer logged. Included in the day 87 report to Houston.');
    await data.stopSessionForPain('mock-session', 4);
    const h = await data.getHouston();
    expect(h.summary?.sessions).toMatchObject([{ state: 'ended' }]);
    expect(h.summary?.contacts).toBeNull();
    const stopped = await data.getStopped();
    expect(stopped).toMatchObject({ sessionId: 'mock-session', rep: 4, groundLink: { state: 'down', backInMin: 12 } });
    expect(stopped.contacts).toEqual([entry]);
    expect(stopped).not.toHaveProperty('crewMedicalOfficerOnBoard');
  });
});

describe('sessions in memory', () => {
  it('has a session in progress until it is stopped, and none after', async () => {
    expect(await data.getSession()).toMatchObject({ state: 'running' });
    await data.stopSessionForPain('mock-session', 4);
    expect(await data.getSession()).toBeNull();
  });

  it('starts a new session in state starting, for the chosen protocol', async () => {
    await data.stopSessionForPain('mock-session', 4);
    const result = await data.startSession('spine_length_rest_v1');
    expect(result).toMatchObject({ ok: true, value: { state: 'starting' } });
    expect(await data.getSession()).toMatchObject({ state: 'starting', protocolId: 'spine_length_rest_v1' });
  });

  it('finish ends the session; artefact and calibration are accepted; reprocess answers ok', async () => {
    const source: DataSource = data;
    expect(await source.markArtifact('mock-session', 2)).toMatchObject({ ok: true });
    expect(await source.calibrateMvc('mock-session')).toMatchObject({ ok: true });
    expect(await source.reprocessSession('mock-session')).toMatchObject({ ok: true });
    expect(await source.finishSession('mock-session')).toMatchObject({ ok: true });
    expect(await data.getSession()).toBeNull();
  });
});

describe('reports in memory', () => {
  it('accepts the daily report, the Oswestry answers and urgent reports without storing them anywhere', async () => {
    const fields = { pain_nrs: 1, stiffness_nrs: 1, pain_locations: [], sleep_hours: null, sleep_quality_nrs: null, red_flags: [], exercise: null };
    const source: DataSource = data;
    expect(await source.saveDailyReport(fields)).toMatchObject({ ok: true });
    expect(await source.saveOswestry([{ item_id: 'pain_intensity', score: 1 }])).toMatchObject({ ok: true });
    expect(await source.addExercise({ modality: 'ared', duration_min: 30 })).toEqual({ ok: false, reason: 'no_context' });
    expect(source.sendsUrgentReports).toBe(true);
    await expect(source.sendUrgentReport('AST-01', ['leg_weakness'])).resolves.toBeUndefined();
  });
});

describe('subscribeReps', () => {
  function collect(): { states: RepState[]; stop: () => void } {
    const states: RepState[] = [];
    const stop = data.subscribeReps('mock-session', (u) => {
      if (u.kind === 'state') states.push(u.state);
    });
    return { states, stop };
  }

  it('starts at rep 4 of 10, pulling down, last rep 3', () => {
    const { states } = collect();
    expect(states).toEqual([{ rep: 4, total: 10, phase: 'pulling', barPercent: 60, last: { rep: 3, seconds: 2.3 }, signal: 'ok' }]);
  });

  it('moves the bar every 450 ms and switches phase at half a cycle', () => {
    const { states } = collect();
    scheduler.advance(450);
    expect(states.at(-1)).toMatchObject({ phase: 'pulling', barPercent: 40, rep: 4 });
    scheduler.advance(450 * 4);
    expect(states.at(-1)).toMatchObject({ phase: 'letting_up', barPercent: 100 });
    scheduler.advance(450);
    expect(states.at(-1)).toMatchObject({ phase: 'letting_up', barPercent: 80 });
  });

  it('counts a rep after a full cycle and stops counting at 10', () => {
    const { states } = collect();
    scheduler.advance(450 * 10);
    expect(states.at(-1)).toMatchObject({ rep: 5, last: { rep: 4, seconds: 2.4 } });
    scheduler.advance(450 * 10 * 20);
    expect(states.at(-1)).toMatchObject({ rep: 10 });
  });

  it('shares one timer between subscribers and stops it when the last one leaves', () => {
    const a = collect();
    const b = collect();
    expect(scheduler.active).toBe(1);
    a.stop();
    expect(scheduler.active).toBe(1);
    b.stop();
    expect(scheduler.active).toBe(0);
    const count = b.states.length;
    scheduler.advance(4500);
    expect(b.states).toHaveLength(count);
  });

  it('resumes from where it was after a new subscription', () => {
    const first = collect();
    scheduler.advance(450 * 10);
    first.stop();
    const { states } = collect();
    expect(states[0]).toMatchObject({ rep: 5 });
  });
});

describe('profile, daily context and devices', () => {
  const fields = {
    date_of_birth: '1990-01-02',
    sex_at_birth: 'male',
    launch_date: '2026-08-20',
    landing_date: null,
    height_standing_cm: 180,
    height_sitting_cm: 92,
    body_mass_kg: 80,
  } as const;

  it('starts with no profile and no context', async () => {
    expect(await data.getProfile()).toBeNull();
    expect(await data.getTodayContext()).toBeNull();
  });

  it('keeps what is saved, in the contract shape', async () => {
    const saved = await data.saveProfile(fields);
    expect(saved).toMatchObject({ ok: true, value: { date_of_birth: '1990-01-02', anthropometry: { height_standing_cm: 180, height_sitting_cm: 92, body_mass_kg: 80 } } });
    expect(await data.getProfile()).toMatchObject({ sex_at_birth: 'male', launch_date: '2026-08-20' });
    const noSitting = await data.saveProfile({ ...fields, height_sitting_cm: null });
    expect(noSitting.ok && 'height_sitting_cm' in noSitting.value.anthropometry).toBe(false);
    await data.saveTodayContext({ phase: 'inflight', gravity_environment: 'microgravity', mission_day: 87 });
    expect(await data.getTodayContext()).toMatchObject({ date: '2026-11-05', phase: 'inflight', mission_day: 87 });
    await data.saveTodayContext({ phase: 'preflight', gravity_environment: 'earth_1g', mission_day: null });
    expect(await data.getTodayContext()).not.toHaveProperty('mission_day');
  });

  it('lists two harnesses, one of them degraded', async () => {
    const devices = await data.listDevices();
    expect(devices.map((d) => d.device_id)).toEqual(['wb-01', 'wb-02']);
    expect(devices.some((d) => d.status === 'degraded')).toBe(true);
    expect(devices.every((d) => typeof d.battery_pct === 'number')).toBe(true);
  });
});
