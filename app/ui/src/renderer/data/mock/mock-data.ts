import type { BaselineData, CordHistoryData, CordProtocolsData, DayRecord, FirstDayData, SessionData, TodayData } from '../types';

/** Static values copied from the on-board app mock (v11). Mock only: a real DataSource replaces this file. */

const MS_PER_MINUTE = 60_000;
const MS_PER_DAY = 86_400_000;
const ASSESSED_MIN_AGO = 42;
const MEASURED_MIN_AGO = 20;
const SESSION_ID = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';

/** Today as the backend would send it: codes and facts. The words come from strings/codes. */
export function todayData(now: Date): TodayData {
  const startedAt = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 13, 38)).toISOString();
  return {
    level: 'amber',
    recommendations: ['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01'],
    reasons: [
      { featureId: 'stretch.full_spine.peak_elongation_mm', deviationZ: 2.4, contribution: 0.31 },
      { featureId: 'imu.t9.sagittal.rom_deg', deviationZ: -1.5, contribution: 0.22 },
      { featureId: 'symptoms.pain_nrs_7d_mean', deviationZ: 1.8, contribution: 0.18 },
    ],
    trust: {
      confidencePct: 70,
      dataAvailable: 8,
      dataTotal: 10,
      missing: [
        { what: 'emg.latissimus_dorsi.right.median_freq_hz', reason: 'sensor_offline' },
        { what: 'self_report', reason: 'no_data' },
      ],
      assessedAt: new Date(now.getTime() - ASSESSED_MIN_AGO * MS_PER_MINUTE).toISOString(),
    },
    alsoToday: ['TASK_DAILY_REPORT', 'TASK_ODI'],
    lumbarLengthMm: 2.1,
    lumbarMeasuredAt: new Date(now.getTime() - MEASURED_MIN_AGO * MS_PER_MINUTE).toISOString(),
    sessionInProgress: { sessionId: SESSION_ID, protocolId: 'lat_pulldown_standing_v1', startedAt, plannedSets: 4, repsPerSet: 15, totalReps: 60 },
  };
}

const BASELINE_SESSIONS: BaselineData['sessions'] = [
  { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b01', protocolId: 'lat_pulldown_standing_v1', status: 'complete', offsetDays: -30, date: null },
  { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b02', protocolId: 'lat_pulldown_standing_v1', status: 'complete', offsetDays: -23, date: null },
  { sessionId: null, protocolId: 'lat_pulldown_standing_v1', status: 'planned', offsetDays: -16, date: null },
];

export const BASELINE: BaselineData = {
  recorded: 2,
  required: 3,
  referenceVersion: 2,
  builtFromSessions: 2,
  sessions: BASELINE_SESSIONS,
};

export const FIRST_DAY: FirstDayData = {
  crewFirstName: 'Elena',
  todo: ['TASK_BASELINE_SESSION', 'TASK_DAILY_REPORT'],
  baseline: BASELINE,
};

const FIRST_DAY_NUMBER = 58;
const LAST_DAY_NUMBER = 87;
const REPORT_NONE: DayRecord['report'] = null;

/** One entry per mission day 58-87 ending on the date of `now`; levels match the history charts. Deterministic: no randomness in the mock. */
export function buildDays(now: Date): DayRecord[] {
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const days: DayRecord[] = [];
  for (let d = FIRST_DAY_NUMBER; d <= LAST_DAY_NUMBER; d++) {
    const nodata = d >= 80 && d <= 82;
    const amber = d >= 83;
    const lengthMm = nodata ? null : amber ? 1.2 + (d - 83) * 0.22 : -0.4 + ((d * 37) % 9) / 10;
    const pain = nodata ? null : d < 72 ? 2 : d < 83 ? 3 : 4;
    days.push({
      date: new Date(midnight - (LAST_DAY_NUMBER - d) * MS_PER_DAY).toISOString().slice(0, 10),
      day: d,
      level: nodata ? 'nodata' : amber ? 'amber' : 'green',
      lengthMm,
      pain,
      session: nodata
        ? null
        : {
            sessionId: `3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f${String(d).padStart(4, '0')}`,
            state: 'processed',
            protocolId: d % 2 ? 'lat_pulldown_standing_v1' : 'spine_length_rest_v1',
          },
      report: nodata ? REPORT_NONE : { kind: 'daily', redFlags: [] },
    });
  }
  return days;
}

export const SESSION: SessionData = {
  sessionId: SESSION_ID,
  state: 'running',
  protocolId: 'lat_pulldown_standing_v1',
  band: { color: 'green', nominalKg: 25 },
  plannedSets: 4,
  repsPerSet: 15,
  totalReps: 60,
  restBetweenSetsS: 30,
  restSeconds: 120,
  fitItems: ['skin', 'emg', 'ground', 'imu', 'cord'],
  startedAt: '2026-11-05T13:38:00Z',
  steps: [
    { id: 'fit', status: 'done' },
    { id: 'check', status: 'done' },
    { id: 'mvc', status: 'done' },
    { id: 'reps', status: 'now' },
    { id: 'finish', status: 'todo' },
  ],
  sensorChecks: [
    { stream: 'emg_lat_left', status: 'good', state: 'active' },
    { stream: 'emg_lat_right', status: 'weak', state: 'stalled' },
    { stream: 'imu_t9', status: 'good', state: 'active' },
    { stream: 'stretch_spine', status: 'good', state: 'active' },
  ],
  calibration: { commandId: 'c0ffee00-0000-4000-8000-000000000004', acked: true, ackedAt: '2026-11-05T13:37:00Z' },
  stretchedCm: null,
  plannedAt: null,
};

/** Mock cord protocols: the straight back has an Earth reference and a follow-up (so a calculated estimate); max flexion is overdue. */
export const CORD_PROTOCOLS: CordProtocolsData = {
  cadenceDays: 7,
  postures: [
    {
      protocolId: 'spine_resistance_straight_v1',
      due: false,
      dueReason: 'CORD_UP_TO_DATE',
      earthReference: { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c01', measuredAt: '2027-01-20T09:00:00Z', resistanceOhm: 4500, gravity: 'earth_1g' },
      lastFollowUp: { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c02', measuredAt: '2027-02-08T09:00:00Z', resistanceOhm: 5650, gravity: 'microgravity' },
      estimate: {
        referenceLengthMm: 500,
        estimatedLengthMm: 538.5723222423,
        elongationMm: 38.5723222423,
        assumptions: ['ASSUMPTION_UNIFORM_RESISTANCE', 'ASSUMPTION_NEGLIGIBLE_CONTACT_RESISTANCE', 'ASSUMPTION_UNIFORM_DEFORMATION'],
      },
      estimateUnavailable: null,
    },
    {
      protocolId: 'spine_resistance_max_flexion_v1',
      due: true,
      dueReason: 'CORD_FOLLOW_UP_OVERDUE',
      earthReference: { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c03', measuredAt: '2027-01-20T09:05:00Z', resistanceOhm: 5650, gravity: 'earth_1g' },
      lastFollowUp: { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c04', measuredAt: '2027-01-30T09:05:00Z', resistanceOhm: 5700, gravity: 'microgravity' },
      estimate: null,
      estimateUnavailable: 'ESTIMATE_REFERENCE_LENGTH_UNKNOWN',
    },
  ],
};

/** Mock cord history: a valid reference and follow-up of the straight back, an invalid one for low quality, and an aborted flexion session. */
export const CORD_HISTORY: CordHistoryData = {
  postures: [
    {
      protocolId: 'spine_resistance_straight_v1',
      measurements: [
        { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c02', measuredAt: '2027-02-08T09:00:00Z', gravity: 'microgravity', resistanceOhm: 5650, qualityScore: 0.9, valid: true, invalidReason: null },
        { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c05', measuredAt: '2027-02-01T09:00:00Z', gravity: 'microgravity', resistanceOhm: 5100, qualityScore: 0.3, valid: false, invalidReason: 'CORD_INVALID_LOW_QUALITY' },
        { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c01', measuredAt: '2027-01-20T09:00:00Z', gravity: 'earth_1g', resistanceOhm: 4500, qualityScore: 0.9, valid: true, invalidReason: null },
      ],
    },
    {
      protocolId: 'spine_resistance_max_flexion_v1',
      measurements: [
        { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c06', measuredAt: '2027-02-05T09:05:00Z', gravity: 'microgravity', resistanceOhm: null, qualityScore: null, valid: false, invalidReason: 'CORD_INVALID_NOT_COMPLETED' },
        { sessionId: '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1c04', measuredAt: '2027-01-30T09:05:00Z', gravity: 'microgravity', resistanceOhm: 5700, qualityScore: 0.9, valid: true, invalidReason: null },
      ],
    },
  ],
};
