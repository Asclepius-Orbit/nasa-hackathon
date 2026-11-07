import type {
  BaselineData,
  DayRecord,
  FirstDayData,
  OswestryData,
  ReportFormData,
  SessionData,
  SymptomCatalog,
  TodayData,
} from '../types';

/** Static values copied from the on-board app mock (v11). Mock only: a real DataSource replaces this file. */

/** ONE catalogue of warning symptoms, most serious first (contract red_flag enum). Pending clinical validation. */
export const CATALOG: SymptomCatalog = {
  flags: [
    { code: 'saddle_numbness', label: 'Numbness between the legs or around the bottom' },
    { code: 'bladder_or_bowel_dysfunction', label: 'Trouble controlling or passing urine or stool' },
    { code: 'leg_weakness', label: 'Leg weakness' },
    { code: 'radiating_leg_pain', label: 'Pain going down a leg' },
    { code: 'numbness_or_tingling', label: 'Numbness or tingling in a leg or foot' },
    { code: 'severe_night_pain', label: 'Severe pain at night' },
    { code: 'fever_with_back_pain', label: 'Fever with back pain' },
    { code: 'trauma_event', label: 'A knock, fall or strain' },
  ],
  escapes: [
    { code: 'very_severe_pain', label: 'Very severe pain' },
    { code: 'other', label: 'Something else' },
  ],
};

export const TODAY: TodayData = {
  level: 'amber',
  meaning:
    'Amber means a measured change from your own preflight reference that needs action today. The flight surgeon reviews amber days at the next ground pass.',
  whatToDo: 'the two plan items below, and your daily report.',
  plan: [
    {
      id: 'stabilisation',
      title: 'Lumbar stabilisation exercises, 10 min',
      detail: 'Procedure 2.3 in the crew health book. Recommendation REC_LUMBAR_STAB_01.',
      recommendation: 'REC_LUMBAR_STAB_01',
      action: { label: 'Open procedure', route: null, kind: 'quiet' },
    },
    {
      id: 'repeat-session',
      title: 'Repeat the standing lat pulldown session today',
      detail: 'Recommendation REC_SESSION_REPEAT_01.',
      recommendation: 'REC_SESSION_REPEAT_01',
      action: { label: 'Return to session', route: '/session', kind: 'primary' },
    },
  ],
  reasons: [
    { label: 'Lumbar length', text: '2.1 mm above your preflight reference in 1 g.' },
    { label: 'Lumbar curve', text: 'flatter than preflight.' },
    { label: 'Pain, 7-day mean', text: '4 of 10, up from 2.' },
  ],
  trust: {
    confidencePct: 70,
    dataAvailable: 8,
    dataTotal: 10,
    missing: "right latissimus EMG (weak electrode contact) and yesterday's report.",
    assessedMinAgo: 42,
  },
  alsoToday: [
    {
      id: 'daily-report',
      title: 'Daily report',
      detail: 'About 1 minute. 3 questions need an answer; the rest are optional.',
      action: { label: 'Start daily report', route: '/report', kind: 'primary' },
    },
    {
      id: 'oswestry',
      title: 'Weekly Oswestry questionnaire',
      detail: 'Due in 2 days. About 2 minutes.',
      action: { label: 'Answer now', route: '/oswestry', kind: 'quiet' },
    },
  ],
  lumbarLengthMm: 2.1,
  sessionInProgress: { title: 'Standing lat pulldown', sinceGmt: '13:38', totalReps: 10 },
};

export const FIRST_DAY: FirstDayData = {
  crewFirstName: 'Elena',
  todo: [
    {
      id: 'profile',
      title: 'Fill in your profile with the flight surgeon',
      detail: 'Height, weight and spine history. Stays on board, never on the harness link.',
      action: { label: 'Open profile', route: null, kind: 'primary' },
    },
    {
      id: 'pair',
      title: 'Pair the harness',
      detail: 'No harness seen yet.',
      action: { label: 'Pair harness', route: null, kind: 'quiet' },
    },
    {
      id: 'baseline',
      title: 'Record your preflight reference',
      detail: '3 sessions in 1 g before launch.',
      action: { label: 'See the plan', route: '/baseline', kind: 'quiet' },
    },
  ],
};

const FIRST_DAY_NUMBER = 58;
const LAST_DAY_NUMBER = 87;

/** One entry per mission day 58-87; levels match the history charts. Deterministic: no randomness in the mock. */
export function buildDays(): DayRecord[] {
  const days: DayRecord[] = [];
  for (let d = FIRST_DAY_NUMBER; d <= LAST_DAY_NUMBER; d++) {
    const nodata = d >= 80 && d <= 82;
    const amber = d >= 83;
    const lengthMm = nodata ? null : amber ? 1.2 + (d - 83) * 0.22 : -0.4 + ((d * 37) % 9) / 10;
    const pain = nodata ? null : d < 72 ? 2 : d < 83 ? 3 : 4;
    let note = 'Everything inside your reference.';
    if (nodata) note = 'No session and no report: not assessed. No data is not the same as fine.';
    else if (amber)
      note =
        d === 83
          ? 'Lumbar length above your reference for the first time.'
          : `Lumbar length above your reference; 7-day pain mean ${d >= 86 ? 'rising.' : 'steady.'}`;
    days.push({
      day: d,
      level: nodata ? 'nodata' : amber ? 'amber' : 'green',
      lengthMm,
      pain,
      session: nodata ? 'None' : d % 2 ? 'Standing lat pulldown, complete' : 'Spine check, complete',
      report: nodata ? 'Not done' : 'Done',
      note,
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
  title: 'Standing lat pulldown with band',
  band: { color: 'blue', nominalKg: 15 },
  stretchedCm: 40,
  totalReps: 10,
  startedGmt: '13:38',
  plannedGmt: '14:00',
  minutesBeforePlan: 22,
  steps: [
    { id: 'fit', label: 'Fit electrodes, IMU and cord', status: 'done' },
    { id: 'check', label: 'Check the harness is sending', status: 'done' },
    { id: 'mvc', label: 'Calibrate maximum effort', status: 'done' },
    { id: 'reps', label: 'Do the reps', status: 'now' },
    { id: 'finish', label: 'Rest and finish', status: 'todo' },
  ],
  fit: {
    items: [
      {
        id: 'skin',
        label: 'Clean the skin',
        detail: 'with a wipe over both latissimus dorsi, below the shoulder blades on each side.',
      },
      {
        id: 'emg',
        label: 'EMG electrodes, left and right:',
        detail: 'two on each latissimus, 2 cm apart along the muscle, and the reference electrode over the spine.',
      },
      { id: 'imu', label: 'IMU at L3:', detail: 'in the belt pocket at the waist line, arrow pointing to the head.' },
      { id: 'cord', label: 'Stretch cord, L1 to L5:', detail: 'top clamp at L1, bottom clamp at L5, no slack and no twist.' },
    ],
    fittedGmt: '13:31',
  },
  checks: {
    items: [
      { sensor: 'EMG left latissimus', status: 'good', detail: 'Clean signal' },
      {
        sensor: 'EMG right latissimus',
        status: 'weak',
        detail: 'Press both right electrodes firmly for 10 s, or replace them, then check again.',
      },
      { sensor: 'IMU at L3', status: 'good', detail: '100 samples a second, still' },
      { sensor: 'Stretch cord L1 to L5', status: 'good', detail: 'Resting length read' },
      { sensor: 'Battery', status: 'good', detail: '64 %, enough for about 3 h' },
    ],
    continuedWithoutGmt: '13:34',
  },
  calibration: { pulls: 3, savedGmt: '13:37', note: 'Left side only: the right EMG was weak.' },
  countingBy: 'IMU at L3, automatic',
  backup: 'Say “mark rep” or press the foot pedal',
  restSeconds: 120,
};

export const BASELINE: BaselineData = {
  recorded: 2,
  required: 3,
  sessions: [
    { label: 'Session 1', protocol: 'standing lat pulldown', status: 'complete', when: 'L-30' },
    { label: 'Session 2', protocol: 'standing lat pulldown', status: 'complete', when: 'L-23' },
    { label: 'Session 3', protocol: 'standing lat pulldown', status: 'planned', when: 'L-16' },
  ],
  referenceVersion: 2,
  builtFromSessions: 2,
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
