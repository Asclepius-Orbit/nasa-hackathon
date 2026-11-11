/**
 * Words for the codes the backend sends (recommendations, tasks, features, missing reasons, phases, session states,
 * report kinds, fit items, streams, protocols, red flags). The backend never sends prose: the UI writes it, here.
 * An unknown code is shown as it is, never hidden: a newer backend must not make a finding disappear.
 */
import type { DailySelfReport, TaskCode } from '../../contracts/v1';

export type RedFlagCode = NonNullable<DailySelfReport['red_flags']>[number];
export type ActionKind = 'quiet' | 'primary';

/** What a button does: a label, the screen it opens (`null` = no screen yet, shown disabled) and its weight. */
export interface ActionText {
  label: string;
  route: string | null;
  kind: ActionKind;
}

export interface PlanText {
  title: string;
  detail: string;
  action: ActionText | null;
}

const RECOMMENDATIONS: Readonly<Record<string, PlanText>> = {
  REC_LUMBAR_STAB_01: {
    title: 'Lumbar stabilisation exercises, 10 min',
    detail: 'Procedure 2.3 in the crew health book. Recommendation REC_LUMBAR_STAB_01.',
    action: { label: 'Open procedure', route: null, kind: 'quiet' },
  },
  REC_SESSION_REPEAT_01: {
    title: 'Repeat the standing lat pulldown session today',
    detail: 'Recommendation REC_SESSION_REPEAT_01.',
    action: { label: 'Return to session', route: '/session', kind: 'primary' },
  },
  REC_INCREASE_ARED: {
    title: 'Add resistive exercise (ARED) time',
    detail: 'More time on the advanced resistive exercise device. Recommendation REC_INCREASE_ARED.',
    action: null,
  },
  REC_CONTACT_FLIGHT_SURGEON: {
    title: 'Contact the flight surgeon',
    detail: 'Recommendation REC_CONTACT_FLIGHT_SURGEON.',
    action: null,
  },
};

export function recommendationText(code: string): PlanText {
  return RECOMMENDATIONS[code] ?? { title: code, detail: '', action: null };
}

const URGENCIES: Readonly<Record<string, string>> = {
  routine: 'routine, can wait for the next scheduled contact',
  priority: 'priority, needs the flight surgeon soon',
  immediate: 'immediate, needs the flight surgeon now',
};
export const urgencyText = (urgency: string): string => URGENCIES[urgency] ?? urgency;

const ESCALATION_RULES: Readonly<Record<string, string>> = {
  RULE_RED_FLAG_NEURO: 'warning neurological symptom',
  RULE_SYMPTOM_PAIN_TREND: 'pain trend rising',
  RULE_INCOMPLETE_DATA: 'data too incomplete to assess',
  RULE_UNKNOWN_RED_FLAG: 'unrecognised warning symptom',
};
export const escalationRuleText = (code: string): string => ESCALATION_RULES[code] ?? code;

const TASKS: Readonly<Record<string, PlanText>> = {
  TASK_DAILY_REPORT: {
    title: 'Daily report',
    detail: 'About 1 minute. 3 questions need an answer; the rest are optional.',
    action: { label: 'Start daily report', route: '/report', kind: 'primary' },
  },
  TASK_ODI: {
    title: 'Weekly Oswestry questionnaire',
    detail: 'About 2 minutes.',
    action: { label: 'Answer now', route: '/oswestry', kind: 'quiet' },
  },
  TASK_BASELINE_SESSION: {
    title: 'Record your preflight reference',
    detail: 'A session in 1 g before launch, with the same protocol.',
    action: { label: 'See the plan', route: '/baseline', kind: 'quiet' },
  },
  TASK_SESSION: {
    title: 'Do the planned session',
    detail: 'The guided exercise session for today.',
    action: { label: 'Open session', route: '/session', kind: 'primary' },
  },
};

export function taskText(code: TaskCode | (string & {})): PlanText {
  return TASKS[code] ?? { title: code, detail: '', action: null };
}

const FEATURES: Readonly<Record<string, string>> = {
  'stretch.lumbar.peak_elongation_mm': 'Lumbar length',
  'imu.l3.sagittal.rom_deg': 'Lumbar curve',
  'emg.latissimus_dorsi.left.median_freq_hz': 'Left latissimus EMG frequency',
  'emg.latissimus_dorsi.right.median_freq_hz': 'Right latissimus EMG frequency',
  'emg.latissimus_dorsi.left.rms_pct_mvc': 'Left latissimus EMG effort',
  'emg.latissimus_dorsi.right.rms_pct_mvc': 'Right latissimus EMG effort',
  'symptoms.pain_nrs_7d_mean': 'Pain, 7-day mean',
  'symptoms.stiffness_nrs_7d_mean': 'Stiffness, 7-day mean',
  'symptoms.sleep_hours_7d_mean': 'Sleep, 7-day mean',
  'symptoms.red_flag_count_7d': 'Warning symptoms, 7 days',
};

export const featureLabel = (featureId: string): string => FEATURES[featureId] ?? featureId;

/** One driver of the level: the feature in words and how far it is from the astronaut own reference, in standard deviations. */
export function reasonText(featureId: string, deviationZ: number | null): { label: string; text: string } {
  const label = featureLabel(featureId);
  if (deviationZ === null) return { label, text: 'Deviation not known.' };
  const direction = deviationZ < 0 ? 'below' : 'above';
  return { label, text: `${Math.abs(deviationZ).toFixed(1)} standard deviations ${direction} your preflight reference.` };
}

const MISSING_REASONS: Readonly<Record<string, string>> = {
  no_data: 'no data',
  low_quality: 'low quality',
  no_baseline: 'no reference yet',
  stale: 'out of date',
  sensor_offline: 'sensor offline',
  signal_not_raw: 'signal not raw',
};

const MISSING_WHAT: Readonly<Record<string, string>> = {
  self_report: "Today's report",
  daily_context: 'Mission context of the day',
  baseline: 'Preflight reference',
};

/** A missing input in words, e.g. `Right latissimus EMG frequency (sensor offline)`. */
export function missingReasonText(what: string, reason: string): string {
  const name = MISSING_WHAT[what] ?? FEATURES[what] ?? what;
  return `${name} (${MISSING_REASONS[reason] ?? reason})`;
}

const PHASES: Readonly<Record<string, string>> = {
  preflight: 'Preflight, 1 g',
  inflight: 'In flight, microgravity',
  postflight: 'Postflight',
};

export const phaseText = (phase: string): string => PHASES[phase] ?? phase;

const SESSION_STATES: Readonly<Record<string, string>> = {
  starting: 'starting',
  running: 'in progress',
  rejected: 'was refused',
  ended: 'ended',
  processing: 'being processed',
  processed: 'complete',
  processing_failed: 'could not be processed',
};

export const sessionStateText = (state: string): string => SESSION_STATES[state] ?? state;

const REPORT_KINDS: Readonly<Record<string, string>> = {
  daily: 'Daily report done',
  weekly_odi: 'Weekly Oswestry done',
  red_flag: 'Warning symptom reported',
};

export const reportKindText = (kind: string): string => REPORT_KINDS[kind] ?? kind;

const FIT_ITEMS: Readonly<Record<string, { label: string; detail: string }>> = {
  skin: { label: 'Clean the skin', detail: 'with a wipe over both latissimus dorsi, below the shoulder blades on each side.' },
  emg: {
    label: 'EMG electrodes, left and right:',
    detail: 'two on each latissimus, 2 cm apart along the muscle, and the reference electrode over the spine.',
  },
  imu: { label: 'IMU at L3:', detail: 'in the belt pocket at the waist line, arrow pointing to the head.' },
  cord: { label: 'Stretch cord, L1 to L5:', detail: 'top clamp at L1, bottom clamp at L5, no slack and no twist.' },
};

export const fitItemText = (id: string): { label: string; detail: string } => FIT_ITEMS[id] ?? { label: id, detail: '' };

const STREAMS: Readonly<Record<string, string>> = {
  emg_lat_left: 'EMG left latissimus',
  emg_lat_right: 'EMG right latissimus',
  imu_l3: 'IMU at L3',
  stretch_lumbar: 'Stretch cord L1 to L5',
};

export const streamLabel = (stream: string): string => STREAMS[stream] ?? stream;

export const weakStreamText = (weak: { stream: string; state: string }): string => `${streamLabel(weak.stream)} ${weak.state}`;

/** Why a sensor check is good or weak, from the stream state. */
export function checkDetail(status: 'good' | 'weak', state: 'active' | 'stalled' | 'disabled'): string {
  if (status === 'good') return 'Clean signal';
  if (state === 'stalled') return 'The sensor stopped sending. Press the electrodes firmly for 10 s, or replace them, then check again.';
  if (state === 'disabled') return 'The sensor is switched off on the harness.';
  return 'The signal is weak. Check the fit, then check again.';
}

const PROTOCOLS: Readonly<Record<string, string>> = {
  lat_pulldown_standing_v1: 'Standing lat pulldown with band',
  spine_length_rest_v1: 'Spine length check',
};

export const protocolTitle = (protocolId: string): string => PROTOCOLS[protocolId] ?? protocolId;

const PROTOCOLS_SHORT: Readonly<Record<string, string>> = {
  lat_pulldown_standing_v1: 'standing lat pulldown',
  spine_length_rest_v1: 'spine length check',
};

/** Short protocol name for sentences such as "L-30, standing lat pulldown, complete". */
export const protocolShort = (protocolId: string): string => PROTOCOLS_SHORT[protocolId] ?? protocolId;

/** ONE catalogue of warning symptoms (contract red_flag enum). Pending clinical validation. */
export const redFlagLabels: Readonly<Record<RedFlagCode, string>> = {
  saddle_numbness: 'Numbness between the legs or around the bottom',
  bladder_or_bowel_dysfunction: 'Trouble controlling or passing urine or stool',
  leg_weakness: 'Leg weakness',
  radiating_leg_pain: 'Pain going down a leg',
  numbness_or_tingling: 'Numbness or tingling in a leg or foot',
  severe_night_pain: 'Severe pain at night',
  fever_with_back_pain: 'Fever with back pain',
  trauma_event: 'A knock, fall or strain',
};
