import { beforeEach, describe, expect, it } from 'vitest';

import type { BaselineView, FirstDayResult, HistoryView, HoustonView, OutboxView, RepStateView, SessionView, StripView, TodayView } from '../../../shared/api';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
import type { RepsUpdate } from '../types';
import { RealDataSource } from './RealDataSource';

let api: FakeAsclepiusApi;
let data: RealDataSource;
beforeEach(() => {
  api = new FakeAsclepiusApi();
  data = new RealDataSource(api.views, api.reps, api);
});

describe('getStrip', () => {
  it('maps the strip example: harness with weak streams as data, ground link unknown', async () => {
    api.viewData.strip = example<StripView>('ui-strip');
    expect(await data.getStrip()).toEqual({
      missionDay: 87,
      missionStart: '2026-11-15',
      flightPhase: 'inflight',
      harness: { id: 'wb-01', batteryPct: 18, weakStreams: [{ stream: 'stretch_lumbar', state: 'stalled' }], degraded: true, stale: false, lastSeenAt: '2026-11-15T09:58:00Z' },
      groundLink: { state: 'down', backInMin: 12 },
      lastContactMinAgo: 45,
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
      groundLink: { state: 'up' },
      lastContactMinAgo: 5,
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

describe('getHouston', () => {
  it('maps the summary and the outbox as codes and nulls, never text', async () => {
    api.viewData.houston = example<HoustonView>('ui-houston');
    api.viewData.outbox = example<OutboxView>('ui-outbox');
    expect(await data.getHouston()).toEqual({
      crewCode: 'AST-01',
      summary: {
        date: '2027-02-09',
        missionDay: 87,
        revision: 2,
        asOf: '2027-02-09T09:00:00Z',
        level: 'amber',
        confidencePct: 74,
        dataAvailable: 5,
        dataTotal: 6,
        drivers: [
          { featureId: 'stretch.lumbar.peak_elongation_mm', deviationZ: 2.4 },
          { featureId: 'symptoms.pain_nrs_7d_mean', deviationZ: 1.8 },
        ],
        redFlags: [],
        sessions: [{ sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03', state: 'processed', protocolId: 'lat_pulldown_standing_v1' }],
        recommendations: ['REC_LUMBAR_STAB_01', 'REC_INCREASE_ARED'],
        escalation: { required: false, urgency: null, reasons: [] },
        contacts: null,
      },
      outbox: [
        { date: '2027-02-09', missionDay: 87, revision: 2, state: 'queued', enqueuedAt: '2027-02-09T09:00:00Z', receivedAt: null, attempts: 3 },
        { date: '2027-02-09', missionDay: 87, revision: 1, state: 'sent', enqueuedAt: '2027-02-09T06:00:00Z', receivedAt: '2027-02-09T06:00:02Z', attempts: 1 },
        { date: '2027-02-08', missionDay: 86, revision: 1, state: 'sent', enqueuedAt: '2027-02-08T09:00:00Z', receivedAt: '2027-02-08T09:05:00Z', attempts: 2 },
      ],
      groundLink: { state: 'down', backInMin: 12 },
      lastContactAt: '2027-02-09T06:00:02Z',
      transport: 'simulated',
      contentValidated: false,
    });
    expect(api.calls).toEqual(expect.arrayContaining(['views.houston', 'views.outbox']));
  });

  it('keeps nothing-yet as nulls and an empty outbox: no summary, no link, no contact', async () => {
    api.viewData.houston = example<HoustonView>('ui-houston.empty');
    api.viewData.outbox = example<OutboxView>('ui-outbox.empty');
    expect(await data.getHouston()).toEqual({
      crewCode: 'AST-01',
      summary: null,
      outbox: [],
      groundLink: null,
      lastContactAt: null,
      transport: 'simulated',
      contentValidated: false,
    });
  });

  it('maps a nodata summary with its nulls', async () => {
    const houston = example<HoustonView>('ui-houston');
    const nodata = {
      ...houston.summary,
      level: 'nodata',
      assessment_id: null,
      confidence: null,
      data_available: null,
      data_total: null,
      drivers: [],
      recommendations: [],
      escalation: null,
    } as unknown as HoustonView['summary'];
    api.viewData.houston = { ...houston, summary: nodata };
    api.viewData.outbox = example<OutboxView>('ui-outbox.empty');
    const { summary } = await data.getHouston();
    expect(summary).toMatchObject({ level: 'nodata', confidencePct: null, dataAvailable: null, dataTotal: null, drivers: [], escalation: null });
  });

  it('fails when the outbox fails: half a screen is not shown', async () => {
    api.viewData.houston = example<HoustonView>('ui-houston');
    api.viewErrors.outbox = 'unavailable';
    await expect(data.getHouston()).rejects.toThrow(/^unavailable$/);
  });
});

describe('static form data', () => {
  it('serves the symptom catalogue, the report form and the Oswestry items (UI choices, not crew data)', async () => {
    expect((await data.getCatalog()).flags).toHaveLength(8);
    expect((await data.getReportForm()).painRegions.length).toBeGreaterThan(0);
    const { items } = await data.getOswestry();
    expect(items).toHaveLength(6);
    expect(items[0]?.id).toBe('pain_intensity');
    expect(api.calls).toEqual([]);
  });
});

describe('sessions', () => {
  const SID = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';

  it('starts a protocol through the bridge and passes the result on', async () => {
    expect(await data.startSession('lat_pulldown_standing_v1')).toEqual({ ok: true, value: { sessionId: 'started-session', state: 'starting' } });
    expect(api.lastArg['sessions.start']).toEqual({ protocolId: 'lat_pulldown_standing_v1' });
    api.forced.set('sessions.start', { ok: false, reason: 'no_harness' });
    expect(await data.startSession('spine_length_rest_v1')).toEqual({ ok: false, reason: 'no_harness' });
  });

  it('after a start, getSession follows that session even when Today lists none in progress', async () => {
    api.viewData.today = example<TodayView>('ui-today');
    api.viewData.sessions = { 'started-session': { ...example<SessionView>('ui-session'), session_id: 'started-session', state: 'starting' } };
    expect(await data.getSession()).toBeNull();
    await data.startSession('lat_pulldown_standing_v1');
    expect(await data.getSession()).toMatchObject({ sessionId: 'started-session', state: 'starting' });
  });

  it('remembers the session Today showed, so it is still shown once it is ended or failed', async () => {
    api.viewData.today = example<TodayView>('ui-today.nodata');
    api.viewData.sessions = { [SID]: example<SessionView>('ui-session') };
    expect(await data.getSession()).toMatchObject({ sessionId: SID });
    api.viewData.today = example<TodayView>('ui-today');
    api.viewData.sessions = { [SID]: { ...example<SessionView>('ui-session'), state: 'processing_failed' } };
    expect(await data.getSession()).toMatchObject({ sessionId: SID, state: 'processing_failed' });
  });

  it('answers null when the remembered session is gone for the backend (not_found)', async () => {
    api.viewData.today = example<TodayView>('ui-today');
    await data.startSession('lat_pulldown_standing_v1');
    api.viewErrors.session = 'not_found';
    expect(await data.getSession()).toBeNull();
  });

  it('fails the session read for any other error, never answering null for it', async () => {
    api.viewData.today = example<TodayView>('ui-today');
    await data.startSession('lat_pulldown_standing_v1');
    api.viewErrors.session = 'unavailable';
    await expect(data.getSession()).rejects.toThrow('unavailable');
  });

  it('stops for pain with the rep and remembers it for the Stopped screen', async () => {
    await data.stopSessionForPain(SID, 4);
    expect(api.lastArg['sessions.stop']).toEqual({ sessionId: SID, reason: 'aborted_pain', repIndex: 4 });
    api.viewData.strip = example<StripView>('ui-strip');
    expect(await data.getStopped()).toMatchObject({ sessionId: SID, rep: 4, groundLink: { state: 'down', backInMin: 12 } });
  });

  it.each(['locked', 'invalid', 'conflict', 'unavailable'] as const)('rejects a stop the backend refused (%s): nothing is recorded', async (reason) => {
    api.forced.set('sessions.stop', reason === 'invalid' ? { ok: false, reason, fields: [] } : { ok: false, reason });
    await expect(data.stopSessionForPain(SID, 4)).rejects.toThrow(reason);
    api.viewData.strip = example<StripView>('ui-strip');
    expect(await data.getStopped()).toMatchObject({ sessionId: null, rep: null });
  });

  it('rejects a stop when the bridge itself fails', async () => {
    api.rejecting.add('sessions.stop');
    await expect(data.stopSessionForPain(SID, null)).rejects.toThrow('bridge down');
  });

  it('keeps the rep unknown (null) when the counter had none, never inventing one', async () => {
    await data.stopSessionForPain(SID, null);
    api.viewData.strip = example<StripView>('ui-strip');
    expect((await data.getStopped()).rep).toBeNull();
  });

  it('finishes with reason completed; marks an artefact and calibrates; reprocesses', async () => {
    expect(await data.finishSession(SID)).toMatchObject({ ok: true });
    expect(api.lastArg['sessions.stop']).toEqual({ sessionId: SID, reason: 'completed' });
    await data.markArtifact(SID, 3);
    expect(api.lastArg['sessions.markArtifact']).toEqual({ sessionId: SID, repIndex: 3 });
    await data.calibrateMvc(SID);
    expect(api.lastArg['sessions.calibrateMvc']).toEqual({ sessionId: SID });
    expect(await data.reprocessSession(SID)).toEqual({ ok: true, value: { sessionId: SID } });
    api.forced.set('sessions.reprocess', { ok: false, reason: 'conflict' });
    expect(await data.reprocessSession(SID)).toEqual({ ok: false, reason: 'conflict' });
  });
});

describe('Stopped screen data', () => {
  it('lists the logged contacts of the backend as GMT lines and takes the ground link from the strip', async () => {
    api.viewData.strip = example<StripView>('ui-strip.unpaired');
    api.contactsValue = [{ contactId: 'c1', at: '2026-11-05T13:52:00Z', sessionId: null }];
    const stopped = await data.getStopped();
    expect(stopped).toEqual({
      sessionId: null,
      rep: null,
      groundLink: { state: 'up' },
      contacts: [{ atGmt: '13:52', text: '13:52 GMT: call to the crew medical officer logged.' }],
    });
    expect(stopped).not.toHaveProperty('crewMedicalOfficerOnBoard');
  });

  it('keeps an unknown ground link null and fails when a part cannot be read', async () => {
    api.viewData.strip = { ...example<StripView>('ui-strip'), ground_link: null };
    expect((await data.getStopped()).groundLink).toBeNull();
    api.viewErrors.strip = 'unavailable';
    await expect(data.getStopped()).rejects.toThrow('unavailable');
  });

  it('logs a call with the given session and returns its entry; a refusal rejects', async () => {
    const entry = await data.logCrewMedicalOfficerCall('S-1');
    expect(api.lastArg['contacts.logOfficerCall']).toEqual({ sessionId: 'S-1' });
    expect(entry).toEqual({ atGmt: '13:52', text: '13:52 GMT: call to the crew medical officer logged.' });
    api.forced.set('contacts.logOfficerCall', { ok: false, reason: 'unavailable' });
    await expect(data.logCrewMedicalOfficerCall(null)).rejects.toThrow('unavailable');
  });
});

describe('reports and urgent', () => {
  const fields = { pain_nrs: 3, stiffness_nrs: 2, pain_locations: [], sleep_hours: null, sleep_quality_nrs: null, red_flags: [], exercise: null };

  it('saves the daily report and the Oswestry answers through the bridge', async () => {
    expect(await data.saveDailyReport(fields)).toMatchObject({ ok: true });
    expect(api.lastArg['reports.saveDaily']).toEqual(fields);
    const items = [{ item_id: 'pain_intensity', score: 2 }];
    expect(await data.saveOswestry(items)).toMatchObject({ ok: true });
    expect(api.lastArg['reports.saveOswestry']).toEqual({ items });
    api.forced.set('reports.saveDaily', { ok: false, reason: 'invalid', fields: [{ pointer: '/pain_nrs', message: 'too high' }] });
    expect(await data.saveDailyReport(fields)).toEqual({ ok: false, reason: 'invalid', fields: [{ pointer: '/pain_nrs', message: 'too high' }] });
  });

  it('retries the exercise through the bridge and passes its answer on', async () => {
    const entry = { modality: 'ared', duration_min: 30 } as const;
    expect(await data.addExercise(entry)).toMatchObject({ ok: true });
    expect(api.lastArg['reports.addExercise']).toEqual(entry);
    api.forced.set('reports.addExercise', { ok: false, reason: 'no_context' });
    expect(await data.addExercise(entry)).toEqual({ ok: false, reason: 'no_context' });
  });

  it('sends urgent reports for real: red flags apart from the other symptoms, for the given crew member', async () => {
    expect(data.sendsUrgentReports).toBe(true);
    await data.sendUrgentReport('AST-02', ['leg_weakness', 'other', 'very_severe_pain', 'saddle_numbness']);
    expect(api.lastArg['urgent.send']).toEqual({
      crewId: 'AST-02',
      red_flags: ['leg_weakness', 'saddle_numbness'],
      other_symptoms: ['other', 'very_severe_pain'],
    });
  });

  it.each(['locked', 'conflict', 'unavailable'] as const)('rejects an urgent report the backend refused (%s)', async (reason) => {
    api.forced.set('urgent.send', { ok: false, reason });
    await expect(data.sendUrgentReport('AST-01', ['leg_weakness'])).rejects.toThrow(reason);
  });

  it('rejects an urgent report refused as invalid', async () => {
    api.forced.set('urgent.send', { ok: false, reason: 'invalid', fields: [] });
    await expect(data.sendUrgentReport('AST-01', ['leg_weakness'])).rejects.toThrow('invalid');
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

describe('profile, daily context and devices', () => {
  const fields = {
    date_of_birth: '1990-01-02',
    sex_at_birth: 'male',
    launch_date: null,
    landing_date: null,
    height_standing_cm: 180,
    height_sitting_cm: null,
    body_mass_kg: 80,
  } as const;

  it('reads the profile and today\'s context from the bridge, null when none is stored', async () => {
    expect(await data.getProfile()).toEqual(api.profileValue);
    expect(await data.getTodayContext()).toEqual(api.contextValue);
    api.profileValue = null;
    api.contextValue = null;
    expect(await data.getProfile()).toBeNull();
    expect(await data.getTodayContext()).toBeNull();
  });

  it('passes a rejection of the bridge on', async () => {
    api.readErrors = { profile: 'locked', context: 'unavailable', devices: 'not_found' };
    await expect(data.getProfile()).rejects.toThrow('locked');
    await expect(data.getTodayContext()).rejects.toThrow('unavailable');
    await expect(data.listDevices()).rejects.toThrow('not_found');
  });

  it('sends the fields untouched and returns the write result, failures included', async () => {
    const saved = await data.saveProfile(fields);
    expect(saved.ok).toBe(true);
    expect(api.lastProfileFields).toEqual(fields);
    api.profileWrite = { ok: false, reason: 'invalid', fields: [{ pointer: '/date_of_birth', message: 'bad' }] };
    expect(await data.saveProfile(fields)).toEqual(api.profileWrite);
    const context = { phase: 'preflight', gravity_environment: 'earth_1g', mission_day: null } as const;
    expect((await data.saveTodayContext(context)).ok).toBe(true);
    expect(api.lastContextFields).toEqual(context);
    api.contextWrite = { ok: false, reason: 'conflict' };
    expect(await data.saveTodayContext(context)).toEqual({ ok: false, reason: 'conflict' });
  });

  it('lists the devices the bridge saw', async () => {
    expect(await data.listDevices()).toEqual([]);
    api.devicesValue = [{ schema_version: '1.0.0', astronaut_id: 'AST-01', device_id: 'wb-09', timestamp: '2026-11-13T10:03:00Z', status: 'ok', source: 'device' }];
    expect((await data.listDevices()).map((d) => d.device_id)).toEqual(['wb-09']);
  });
});
