import { redFlagLabels } from '../../strings/codes';
import type { BaselineData, DayRecord, FirstDayData, OswestryData, ReportFormData, SessionData, SymptomCatalog, TodayData } from '../types';

/** Static values copied from the on-board app mock (v11). Mock only: a real DataSource replaces this file. */

/** ONE catalogue of warning symptoms, most serious first (contract red_flag enum). Pending clinical validation. */
const RED_FLAG_ORDER = [
  'saddle_numbness',
  'bladder_or_bowel_dysfunction',
  'leg_weakness',
  'radiating_leg_pain',
  'numbness_or_tingling',
  'severe_night_pain',
  'fever_with_back_pain',
  'trauma_event',
] as const;

export const CATALOG: SymptomCatalog = {
  flags: RED_FLAG_ORDER.map((code) => ({ code, label: redFlagLabels[code] })),
  escapes: [
    { code: 'very_severe_pain', label: 'Very severe pain' },
    { code: 'other', label: 'Something else' },
  ],
};

const MS_PER_MINUTE = 60_000;
const MS_PER_DAY = 86_400_000;
const ASSESSED_MIN_AGO = 42;
const SESSION_ID = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';

/** Today as the backend would send it: codes and facts. The words come from strings/codes. */
export function todayData(now: Date): TodayData {
  const startedAt = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 13, 38)).toISOString();
  return {
    level: 'amber',
    recommendations: ['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01'],
    reasons: [
      { featureId: 'stretch.lumbar.peak_elongation_mm', deviationZ: 2.4, contribution: 0.31 },
      { featureId: 'imu.l3.sagittal.rom_deg', deviationZ: -1.5, contribution: 0.22 },
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
    sessionInProgress: { sessionId: SESSION_ID, protocolId: 'lat_pulldown_standing_v1', startedAt, totalReps: 10 },
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

export const REPORT_FORM: ReportFormData = {
  catalog: CATALOG,
  painRegions: [
    { value: 'cervical', label: 'Neck' },
    { value: 'thoracic', label: 'Upper back' },
    { value: 'lumbar', label: 'Lower back' },
    { value: 'sacral', label: 'Tailbone' },
  ],
  painSides: [
    { value: 'left', label: 'Left' },
    { value: 'midline', label: 'Middle' },
    { value: 'right', label: 'Right' },
    { value: 'bilateral', label: 'Both sides' },
  ],
  sleepHours: [
    { value: 3, label: 'Under 4 h' },
    { value: 4, label: '4 h' },
    { value: 5, label: '5 h' },
    { value: 6, label: '6 h' },
    { value: 7, label: '7 h' },
    { value: 8, label: '8 h' },
    { value: 9, label: '9 h or more' },
    { value: null, label: 'Not sure' },
  ],
  exerciseKinds: [
    { value: 'ARED', label: 'ARED' },
    { value: 'CEVIS', label: 'CEVIS' },
    { value: 'T2', label: 'T2 treadmill' },
    { value: 'none', label: 'None yet' },
  ],
  exerciseMinutes: [
    { value: 15, label: '15 min' },
    { value: 30, label: '30 min' },
    { value: 45, label: '45 min' },
    { value: 60, label: '60 min' },
    { value: 90, label: '90 min or more' },
  ],
};

export const SESSION: SessionData = {
  sessionId: SESSION_ID,
  state: 'running',
  protocolId: 'lat_pulldown_standing_v1',
  band: { color: 'blue', nominalKg: 15 },
  totalReps: 10,
  restSeconds: 120,
  fitItems: ['skin', 'emg', 'imu', 'cord'],
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
    { stream: 'imu_l3', status: 'good', state: 'active' },
    { stream: 'stretch_lumbar', status: 'good', state: 'active' },
  ],
  calibration: { commandId: 'c0ffee00-0000-4000-8000-000000000004', acked: true, ackedAt: '2026-11-05T13:37:00Z' },
  stretchedCm: null,
  plannedAt: null,
};

/** Reduced item set, statements scored 0-5. Item ids follow the contract ODI sections where one exists. */
export const ODI: OswestryData = {
  items: [
    {
      id: 'pain_intensity',
      title: 'Pain intensity',
      options: ['No pain', 'Very mild', 'Moderate', 'Fairly severe', 'Very severe', 'Worst imaginable'],
    },
    {
      id: 'personal_care',
      title: 'Personal care',
      options: [
        'Normal, no extra pain',
        'Normal, but painful',
        'Slow and careful',
        'Need some help',
        'Need help every day',
        'Cannot manage',
      ],
    },
    {
      id: 'lifting',
      title: 'Lifting and pulling',
      options: [
        'Heavy loads, no pain',
        'Heavy loads, with pain',
        'Only if well placed',
        'Light to medium only',
        'Very light only',
        'Cannot lift anything',
      ],
    },
    {
      id: 'sleeping',
      title: 'Sleeping',
      options: ['Never disturbed', 'Sometimes disturbed', 'Under 6 h', 'Under 4 h', 'Under 2 h', 'Cannot sleep'],
    },
    {
      id: 'working',
      title: 'Working',
      options: ['Full workload', 'Full, with pain', 'Most tasks', 'Some tasks', 'Very few tasks', 'Cannot work'],
    },
    {
      id: 'exercise_countermeasures',
      title: 'Exercise and countermeasures',
      options: ['As planned', 'As planned, with pain', 'Shortened', 'Much shortened', 'Barely', 'Cannot do them'],
    },
  ],
};
