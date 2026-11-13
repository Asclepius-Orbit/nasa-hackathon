import type { DisplayLevel } from './common';
import { levelLabel } from './common';

const PLAN_COUNT_WORDS: Record<number, string> = { 1: 'one', 2: 'two' };

/** Copy of the Today screen. Texts that come from the engine (meaning, plan, reasons) live in the data. */
export const today = {
  title: 'Today',
  loading: 'Loading today.',
  error: 'Today could not be loaded.',
  whatToDo: 'What to do today:',
  noAssessment: 'There is no assessment yet, so there is nothing to explain and no confidence to give.',
  sessionRunning: 'Session in progress',
  sessionRunningDetail: 'Return to it from here or from the Session tab.',
  openSession: 'Return to session',
  noLength: 'No data',
  planLabel: 'Plan for today',
  warningSummary: (count: number): string => `Warning symptoms: the ${String(count)} that mean press Urgent symptom`,
  meaningSummary: 'What green, amber and red mean',
  meanings: {
    green: 'Inside your reference. Keep your usual routine and daily report.',
    amber: "A change from your reference. Do today's plan; the flight surgeon reviews it at the next pass.",
    red: 'The crew medical officer and the flight surgeon are alerted. Stop the protocol exercise until cleared.',
  },
  verdicts: {
    green: 'Green means every measured value is inside your own preflight reference.',
    amber:
      'Amber means a measured change from your own preflight reference that needs action today. The flight surgeon reviews amber days at the next ground pass.',
    red: 'Red means a measured change that needs the crew medical officer now. The flight surgeon is alerted.',
    nodata: 'No data means nothing has been assessed. It does not mean everything is fine.',
  },
  todoNow: {
    // The daily report is named only while it is still due today (TASK_DAILY_REPORT in also_today).
    green: (dailyReportDue: boolean): string => (dailyReportDue ? 'your usual routine and your daily report.' : 'your usual routine.'),
    amber: (plan: number, dailyReportDue: boolean): string => {
      const items = plan === 0 ? null : `the ${PLAN_COUNT_WORDS[plan] ?? String(plan)} plan ${plan === 1 ? 'item' : 'items'} below`;
      if (items === null) return dailyReportDue ? 'your daily report.' : 'nothing is listed in the plan yet.';
      return dailyReportDue ? `${items}, and your daily report.` : `${items}.`;
    },
    red: 'stop the protocol exercise and call the crew medical officer.',
    nodata: 'the things listed under Also today. Until something is measured there is no assessment.',
  },
  why: (level: DisplayLevel): string => `Why ${levelLabel(level).toLowerCase()}`,
  confidence: 'Confidence',
  confidenceNote: 'How much the engine trusts this result.',
  dataAvailable: 'Data available',
  dataOf: (n: number, total: number): string => `${String(n)} of ${String(total)}`,
  missing: (text: string): string => `Missing: ${text}`,
  assessed: 'Assessed',
  assessedNote: 'It updates after each session and report.',
  alsoToday: 'Also today',
} as const;

export const confidenceValue = (pct: number): string => `${String(pct)} %`;
export const minutesAgo = (min: number): string => `${String(min)} min ago`;

/** Session in progress with no live rep count: only what the contract gives (start, planned sets and reps). */
export const inProgressSince = (sinceGmt: string, sets: number | null, repsPerSet: number | null, total: number | null): string => {
  if (sets !== null && repsPerSet !== null) return `In progress since ${sinceGmt} GMT, ${String(sets)} sets of ${String(repsPerSet)} reps.`;
  return total === null ? `In progress since ${sinceGmt} GMT.` : `In progress since ${sinceGmt} GMT, ${String(total)} reps.`;
};

export const inProgressLine = (sinceGmt: string, rep: number, total: number | null): string =>
  total === null || rep > total
    ? `In progress since ${sinceGmt} GMT, rep ${String(rep)}.`
    : `In progress since ${sinceGmt} GMT, rep ${String(rep)} of ${String(total)}.`;

export const spine = {
  label: (length: string): string =>
    `Side view of the spine. Lumbar length ${length} against the preflight profile. EMG over the latissimus dorsi on both sides, ground on the iliac crest, IMU at T9, stretch cord from C3 to S1.`,
  caption: 'Side view, back to the right. The cord measures length from C3 to S1; the curve comes from the IMU at T9.',
  noLength: 'No data',
  measured: (day: string): string => `Lumbar length measured ${day}.`,
  cervical: 'Cervical',
  lumbar: 'Lumbar',
  emg: 'EMG',
  emgWhere1: 'latissimus',
  emgWhere2: 'dorsi, L and R',
  cord: 'Stretch cord',
  cordWhere: 'C3 to S1',
  gnd: 'GND',
  gndWhere: 'iliac crest',
  imu: 'IMU',
  imuWhere: 'at T9',
  preflight: 'Preflight',
  today: 'Today',
} as const;
