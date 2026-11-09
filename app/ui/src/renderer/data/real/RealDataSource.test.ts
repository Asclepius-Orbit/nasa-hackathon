import { beforeEach, describe, expect, it } from 'vitest';

import type { BaselineView, FirstDayResult, HistoryView, RepStateView, SessionView, StripView, TodayView } from '../../../shared/api';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
import type { RepsUpdate } from '../types';
import { RealDataSource } from './RealDataSource';

let api: FakeAsclepiusApi;
let data: RealDataSource;
beforeEach(() => {
  api = new FakeAsclepiusApi();
  data = new RealDataSource(api.views, api.reps);
});

describe('getStrip', () => {
  it('maps the strip example: harness with weak streams as data, ground link unknown', async () => {
    api.viewData.strip = example<StripView>('ui-strip');
    expect(await data.getStrip()).toEqual({
      missionDay: 87,
      missionStart: '2026-11-15',
      flightPhase: 'inflight',
      harness: { id: 'wb-01', batteryPct: 18, weakStreams: [{ stream: 'stretch_lumbar', state: 'stalled' }], degraded: true, stale: false, lastSeenAt: '2026-11-15T09:58:00Z' },
      groundLink: null,
      lastContactMinAgo: null,
      reportsDue: 1,
    });
  });

  it('keeps every unknown of an unpaired strip as null, never a default', async () => {
    api.viewData.strip = example<StripView>('ui-strip.unpaired');
    expect(await data.getStrip()).toEqual({
      missionDay: null,
      missionStart: null,
      flightPhase: null,
      harness: null,
      groundLink: null,
      lastContactMinAgo: null,
      reportsDue: 2,
    });
  });

  it('counts the two reports due and keeps an unreported battery null', async () => {
    const strip = example<StripView>('ui-strip');
    api.viewData.strip = {
      ...strip,
      harness: strip.harness && { ...strip.harness, battery_pct: null },
      reports_due: { daily: false, weekly_odi: false },
      last_contact_min_ago: 7,
    };
    const result = await data.getStrip();
    expect(result.reportsDue).toBe(0);
    expect(result.harness?.batteryPct).toBeNull();
    expect(result.lastContactMinAgo).toBe(7);
  });
});

describe('stale harness', () => {
  it('maps a harness whose last status is old: stale and when it was last seen', async () => {
    const strip = example<StripView>('ui-strip');
    api.viewData.strip = { ...strip, harness: strip.harness && { ...strip.harness, stale: true, last_seen_at: '2026-11-03T07:15:00Z' } };
    expect((await data.getStrip()).harness).toMatchObject({ id: 'wb-01', stale: true, lastSeenAt: '2026-11-03T07:15:00Z' });
  });
});

describe('getToday', () => {
  it('maps the today example to codes and facts, with the confidence as a percentage', async () => {
    api.viewData.today = example<TodayView>('ui-today');
    expect(await data.getToday()).toEqual({
      level: 'amber',
      recommendations: ['REC_LUMBAR_STAB_01', 'REC_INCREASE_ARED'],
      reasons: [
        { featureId: 'stretch.lumbar.peak_elongation_mm', deviationZ: 2.4, contribution: 0.31 },
        { featureId: 'symptoms.pain_nrs_7d_mean', deviationZ: 1.8, contribution: 0.18 },
      ],
      trust: {
        confidencePct: 74,
        dataAvailable: 5,
        dataTotal: 6,
        missing: [{ what: 'emg.latissimus_dorsi.right.median_freq_hz', reason: 'sensor_offline' }],
        assessedAt: '2027-02-09T09:00:00Z',
      },
      alsoToday: ['TASK_DAILY_REPORT'],
      lumbarLengthMm: 6.4,
      lumbarMeasuredAt: '2027-02-09T08:00:00Z',
      sessionInProgress: null,
    });
  });

  it('keeps nodata explicit: no trust, no length, and the running session survives', async () => {
    api.viewData.today = example<TodayView>('ui-today.nodata');
    expect(await data.getToday()).toEqual({
      level: 'nodata',
      recommendations: [],
      reasons: [],
      trust: null,
      alsoToday: ['TASK_DAILY_REPORT', 'TASK_ODI'],
      lumbarLengthMm: null,
      lumbarMeasuredAt: null,
      sessionInProgress: {
        sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01',
        protocolId: 'lat_pulldown_standing_v1',
        startedAt: '2026-11-13T10:02:31Z',
        totalReps: 10,
      },
    });
  });
});

describe('getFirstDay', () => {
  it('maps the tasks, the baseline and keeps the local first name', async () => {
    const view = example<FirstDayResult>('ui-first-day');
    api.viewData.firstDay = { ...view, crewFirstName: 'Elena' };
    const result = await data.getFirstDay();
    expect(result.crewFirstName).toBe('Elena');
    expect(result.todo).toEqual(['TASK_BASELINE_SESSION', 'TASK_DAILY_REPORT']);
    expect(result.baseline.recorded).toBe(2);
    expect(result.baseline.sessions).toHaveLength(3);
  });
});

describe('getBaseline', () => {
  it('maps recorded and planned sessions with their offsets and dates', async () => {
    api.viewData.baseline = example<BaselineView>('ui-baseline');
    expect(await data.getBaseline()).toEqual({
      recorded: 2,
      required: 3,
      referenceVersion: 1,
      builtFromSessions: 2,
      sessions: [
        { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b01', protocolId: 'lat_pulldown_standing_v1', status: 'complete', offsetDays: -30, date: '2026-10-16' },
        { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b02', protocolId: 'lat_pulldown_standing_v1', status: 'complete', offsetDays: -23, date: '2026-10-23' },
        { sessionId: null, protocolId: 'lat_pulldown_standing_v1', status: 'planned', offsetDays: -16, date: '2026-10-30' },
      ],
    });
  });

  it('keeps "no reference yet" and undated sessions as null', async () => {
    api.viewData.baseline = example<BaselineView>('ui-baseline.empty');
    const result = await data.getBaseline();
    expect(result.referenceVersion).toBeNull();
    expect(result.builtFromSessions).toBe(0);
    expect(result.sessions.every((s) => s.date === null && s.sessionId === null)).toBe(true);
  });
});

describe('getHistory', () => {
  it('maps days: nodata stays null, sessions and reports stay codes', async () => {
    api.viewData.history = example<HistoryView>('ui-history');
    const result = await data.getHistory();
    expect(result).toMatchObject({ from: '2027-02-07', to: '2027-02-09', todayDay: 87, preflightRangeMm: { lo: -3.2, hi: 4.1 } });
    expect(result.days[0]).toEqual({ date: '2027-02-07', day: 85, level: 'nodata', lengthMm: null, pain: null, session: null, report: null });
    expect(result.days[1]).toEqual({
      date: '2027-02-08',
      day: 86,
      level: 'green',
      lengthMm: 5,
      pain: 3,
      session: { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03', state: 'processed', protocolId: 'lat_pulldown_standing_v1' },
      report: { kind: 'daily', redFlags: [] },
    });
    expect(result.days[2]?.report).toEqual({ kind: 'weekly_odi', redFlags: [] });
  });

  it('keeps an empty window with no launch date: no days, no today, no range', async () => {
    api.viewData.history = example<HistoryView>('ui-history.empty');
    expect(await data.getHistory()).toEqual({ from: '2026-11-01', to: '2026-11-07', todayDay: null, preflightRangeMm: null, days: [] });
  });
});

describe('getSession', () => {
  const sessionId = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';

  beforeEach(() => {
    api.viewData.today = example<TodayView>('ui-today.nodata');
    api.viewData.sessions = { [sessionId]: example<SessionView>('ui-session') };
  });

  it('opens the session that today reports in progress and maps it to codes', async () => {
    const result = await data.getSession();
    expect(api.calls).toEqual(['views.today', 'views.session']);
    expect(result).toEqual({
      sessionId,
      state: 'running',
      protocolId: 'lat_pulldown_standing_v1',
      band: { color: 'blue', nominalKg: 15 },
      totalReps: 10,
      restSeconds: 120,
      fitItems: ['skin', 'emg', 'imu', 'cord'],
      startedAt: '2026-11-13T10:02:31Z',
      steps: [
        { id: 'fit', status: 'done' },
        { id: 'check', status: 'done' },
        { id: 'mvc', status: 'done' },
        { id: 'reps', status: 'now' },
        { id: 'finish', status: 'todo' },
      ],
      sensorChecks: [
        { stream: 'emg_lat_left', status: 'good', state: 'active' },
        { stream: 'imu_l3', status: 'good', state: 'active' },
        { stream: 'stretch_lumbar', status: 'weak', state: 'stalled' },
      ],
      calibration: { commandId: 'c0ffee00-0000-4000-8000-000000000004', acked: true, ackedAt: '2026-11-13T10:00:06.400Z' },
      stretchedCm: null,
      plannedAt: null,
    });
  });

  it('keeps a protocol without band, reps or rest as null', async () => {
    api.viewData.sessions = { [sessionId]: example<SessionView>('ui-session.spine-check') };
    const result = await data.getSession();
    expect(result).toMatchObject({ band: null, totalReps: null, restSeconds: null, startedAt: null, calibration: null, sensorChecks: [] });
  });

  it('is null when no session is in progress, without asking for one', async () => {
    api.viewData.today = example<TodayView>('ui-today');
    expect(await data.getSession()).toBeNull();
    expect(api.calls).toEqual(['views.today']);
  });
});

describe('errors', () => {
  it.each(['unavailable', 'locked', 'not_found'] as const)('passes %s through, never a fallback value', async (code) => {
    api.viewErrors = { strip: code, today: code, history: code, baseline: code, firstDay: code };
    for (const call of [() => data.getStrip(), () => data.getToday(), () => data.getHistory(), () => data.getBaseline(), () => data.getFirstDay(), () => data.getSession()]) {
      await expect(call()).rejects.toThrow(code);
    }
  });

  it('rejects unavailable by default, like a backend that is down', async () => {
    await expect(data.getStrip()).rejects.toThrow('unavailable');
  });

  it('passes not_found from the session view', async () => {
    api.viewData.today = example<TodayView>('ui-today.nodata');
    api.viewErrors = { session: 'not_found' };
    await expect(data.getSession()).rejects.toThrow('not_found');
  });
});

describe('what #61 to #63 still own', () => {
  it('serves the static form data of the UI: the symptom catalogue and the report form', async () => {
    expect((await data.getCatalog()).flags).toHaveLength(8);
    expect((await data.getReportForm()).painRegions.length).toBeGreaterThan(0);
  });

  it.each([
    ['getStopped', () => data.getStopped()],
    ['getOswestry', () => data.getOswestry()],
    ['getHouston', () => data.getHouston()],
  ])('%s rejects not_built (not "unavailable": the monitor is fine, the feature is not connected yet)', async (_name, call) => {
    await expect(call()).rejects.toThrow(/^not_built$/);
  });

  it.each([
    ['stopSessionForPain', () => data.stopSessionForPain()],
    ['logCrewMedicalOfficerCall', () => data.logCrewMedicalOfficerCall()],
  ])('%s rejects unavailable: the mock never records for the backend', async (_name, call) => {
    await expect(call()).rejects.toThrow(/^unavailable$/);
  });

  it('does not touch the bridge for them', async () => {
    await data.getStopped().catch(() => undefined);
    await data.stopSessionForPain().catch(() => undefined);
    expect(api.calls).toEqual([]);
  });
});

describe('subscribeReps', () => {
  const SID = 'a55e55e0-0000-4000-8000-000000000001';
  const view = (name = 'ui-rep-state'): RepStateView => example<RepStateView>(name);
  function listen(): { seen: RepsUpdate[]; stop: () => void } {
    const seen: RepsUpdate[] = [];
    const stop = data.subscribeReps(SID, (update) => seen.push(update));
    return { seen, stop };
  }

  it('maps the contract examples field by field: null stays null, resting and no_signal are kept', () => {
    const { seen } = listen();
    for (const name of ['ui-rep-state', 'ui-rep-state.first-rep', 'ui-rep-state.resting', 'ui-rep-state.no-signal']) {
      api.emitReps(SID, { kind: 'state', state: view(name) });
    }
    expect(seen).toEqual([
      { kind: 'state', state: { rep: 3, total: 10, phase: 'pulling', barPercent: 42.5, last: { rep: 2, seconds: 4.2 }, signal: 'ok' } },
      { kind: 'state', state: { rep: 1, total: 10, phase: 'pulling', barPercent: null, last: null, signal: 'ok' } },
      { kind: 'state', state: { rep: 3, total: 10, phase: 'resting', barPercent: null, last: { rep: 2, seconds: 4.2 }, signal: 'ok' } },
      { kind: 'state', state: { rep: 3, total: 10, phase: null, barPercent: null, last: { rep: 2, seconds: 4.2 }, signal: 'no_signal' } },
    ]);
  });

  it('keeps an unknown total as null', () => {
    const { seen } = listen();
    api.emitReps(SID, { kind: 'state', state: { ...view(), total: null } });
    expect(seen[0]).toMatchObject({ state: { total: null } });
  });

  it('lost keeps the last known counter without phase or bar, flagged lost; the next state replaces it', () => {
    const { seen } = listen();
    api.emitReps(SID, { kind: 'state', state: view() });
    api.emitReps(SID, { kind: 'lost' });
    api.emitReps(SID, { kind: 'state', state: view('ui-rep-state.resting') });
    expect(seen[1]).toEqual({ kind: 'state', state: { rep: 3, total: 10, phase: null, barPercent: null, last: { rep: 2, seconds: 4.2 }, signal: 'lost' } });
    expect(seen[2]).toMatchObject({ state: { phase: 'resting', signal: 'ok' } });
  });

  it('lost before any state invents no count: it says it is reconnecting, without a rep', () => {
    const { seen } = listen();
    api.emitReps(SID, { kind: 'lost' });
    expect(seen).toEqual([{ kind: 'reconnecting' }]);
  });

  it('end delivers the final count as ended (no phase, no bar, not live) and stops listening', () => {
    const { seen } = listen();
    api.emitReps(SID, { kind: 'end', state: { ...view(), phase: null, progress_pct: null } });
    expect(seen).toEqual([
      { kind: 'state', state: { rep: 3, total: 10, phase: null, barPercent: null, last: { rep: 2, seconds: 4.2 }, signal: 'ended' } },
    ]);
    expect(api.repsListenerCount(SID)).toBe(0);
  });

  it.each([
    ['no_reps', 'none'],
    ['not_found', 'none'],
    ['unavailable', 'unavailable'],
    ['locked', 'locked'],
  ] as const)('error %s becomes %s and stops listening', (code, kind) => {
    const { seen } = listen();
    api.emitReps(SID, { kind: 'error', code });
    expect(seen).toEqual([{ kind }]);
    expect(api.repsListenerCount(SID)).toBe(0);
  });

  it('subscribes for the given session and the returned function unsubscribes', () => {
    const { stop } = listen();
    expect(api.repsListenerCount(SID)).toBe(1);
    stop();
    expect(api.repsListenerCount(SID)).toBe(0);
  });
});
