import type { Assessment, DailySelfReport } from '../../contracts/v1';

/** Traffic light of an assessment (contract `Assessment.level`). */
export type Level = Assessment['level'];
/** A day on the history strip: a level, or `nodata` when nothing was measured. Never a green by default. */
export type DayLevel = Level | 'nodata';
/** Red-flag codes (contract `red_flags` enum), most serious first in the catalogue. */
export type RedFlagCode = NonNullable<DailySelfReport['red_flags']>[number];
/** Choices of the Urgent dialog that are NOT in the contract yet ("Contract change pending" in the mock). */
export type EscapeCode = 'very_severe_pain' | 'other';

export interface SymptomOption<Code extends string> {
  code: Code;
  label: string;
}

export interface SymptomCatalog {
  /** The 8 contract red flags, most serious first. */
  flags: readonly SymptomOption<RedFlagCode>[];
  /** Extra Urgent-dialog choices, not part of the contract. */
  escapes: readonly SymptomOption<EscapeCode>[];
}

export type GroundLink = { state: 'up' } | { state: 'down'; backInMin: number | null };

export interface HarnessStatus {
  id: string;
  batteryPct: number;
  /** Weak-channel note such as "EMG right weak"; `null` when everything is good. */
  note: string | null;
}

/** Facts shown in the always-visible strip and the offline banner. */
export interface StripData {
  missionDay: number;
  /** ISO time of mission start; MET is measured from here. */
  missionStart: string;
  flightPhase: string;
  /** `null` until a harness is paired. */
  harness: HarnessStatus | null;
  groundLink: GroundLink;
  /** `null` when there has been no contact at all. */
  lastContactMinAgo: number | null;
  /** Reports still due today; the badge on the Report tab (0 hides it). */
  reportsDue: number;
}

export interface PlanItem {
  id: string;
  title: string;
  detail: string;
  /** Recommendation code from the engine catalogue, e.g. `REC_LUMBAR_STAB_01`. */
  recommendation: string;
  action: { label: string; route: string | null; kind: 'quiet' | 'primary' };
}

export interface Reason {
  label: string;
  text: string;
}

export interface TrustFacts {
  confidencePct: number;
  dataAvailable: number;
  dataTotal: number;
  /** Plain-words list of the missing data. */
  missing: string;
  assessedMinAgo: number;
}

export interface TodoItem {
  id: string;
  title: string;
  detail: string;
  action: { label: string; route: string | null; kind: 'quiet' | 'primary' };
}

export interface TodayData {
  level: Level;
  /** What the level means, in the engine words. */
  meaning: string;
  whatToDo: string;
  plan: readonly PlanItem[];
  reasons: readonly Reason[];
  trust: TrustFacts;
  alsoToday: readonly TodoItem[];
  /** Lumbar length change vs preflight, shown on the spine figure. */
  lumbarLengthMm: number;
  /** A session in progress, if any (live rep count comes from `subscribeReps`). */
  sessionInProgress: { title: string; sinceGmt: string; totalReps: number } | null;
}

export interface FirstDayData {
  crewFirstName: string;
  todo: readonly TodoItem[];
}

export interface DayRecord {
  day: number;
  level: DayLevel;
  /** Lumbar length vs preflight in mm; `null` = no data (never 0). */
  lengthMm: number | null;
  /** Daily-report pain 0-10; `null` = no report. */
  pain: number | null;
  session: string;
  report: string;
  note: string;
}

export interface HistoryData {
  days: readonly DayRecord[];
  todayDay: number;
  /** The astronaut own preflight range for lumbar length, in mm. */
  preflightRangeMm: { lo: number; hi: number };
}

export interface ChoiceOption<Value> {
  value: Value;
  label: string;
}

export interface ReportFormData {
  catalog: SymptomCatalog;
  /** Mock regions mapped to the contract `PainLocation.region`. */
  painRegions: readonly ChoiceOption<'cervical' | 'thoracic' | 'lumbar' | 'sacral'>[];
  /** Mock sides mapped to the contract `PainLocation.side`. */
  painSides: readonly ChoiceOption<'left' | 'midline' | 'right' | 'bilateral'>[];
  /** Hours of sleep; `null` value = "Not sure". */
  sleepHours: readonly ChoiceOption<number | null>[];
  exerciseKinds: readonly ChoiceOption<string>[];
  exerciseMinutes: readonly ChoiceOption<number>[];
}

export type StepStatus = 'done' | 'now' | 'todo';

export interface SessionStep {
  id: 'fit' | 'check' | 'mvc' | 'reps' | 'finish';
  label: string;
  status: StepStatus;
}

export interface SensorCheck {
  sensor: string;
  status: 'good' | 'weak';
  detail: string;
}

export interface SessionData {
  title: string;
  band: { color: string; nominalKg: number };
  stretchedCm: number;
  totalReps: number;
  startedGmt: string;
  plannedGmt: string;
  minutesBeforePlan: number;
  steps: readonly SessionStep[];
  fit: { items: readonly { id: string; label: string; detail: string }[]; fittedGmt: string };
  checks: { items: readonly SensorCheck[]; continuedWithoutGmt: string | null };
  calibration: { pulls: number; savedGmt: string; note: string };
  countingBy: string;
  backup: string;
  restSeconds: number;
}

export interface RepState {
  rep: number;
  total: number;
  phase: 'pulling' | 'letting_up';
  /** Progress of the current movement, 0-100. */
  barPercent: number;
  /** Last completed rep, if any. */
  last: { rep: number; seconds: number } | null;
}

export interface ContactEntry {
  atGmt: string;
  text: string;
}

export interface StoppedData {
  /** Rep at which the session stopped. */
  rep: number;
  groundLink: GroundLink;
  crewMedicalOfficerOnBoard: boolean;
  contacts: readonly ContactEntry[];
}

export interface BaselineSession {
  label: string;
  protocol: string;
  status: 'complete' | 'planned';
  /** When it happened or is planned, e.g. `L-30`. */
  when: string;
}

export interface BaselineData {
  recorded: number;
  required: number;
  sessions: readonly BaselineSession[];
  referenceVersion: number;
  builtFromSessions: number;
}

export interface OdiItem {
  id: string;
  title: string;
  /** Statements scored 0..5, in order. */
  options: readonly string[];
}

export interface OswestryData {
  items: readonly OdiItem[];
}

export interface OutboxItem {
  label: string;
  state: 'queued' | 'sent';
  /** When the ground station received it; only for `sent`. */
  receivedGmt: string | null;
}

export interface HoustonData {
  crewCode: string;
  day: number;
  level: Level;
  confidencePct: number;
  dataAvailable: number;
  dataTotal: number;
  drivers: string;
  warningSymptoms: string;
  sessions: string;
  contacts: string;
  plan: readonly string[];
  outbox: readonly OutboxItem[];
  groundLink: GroundLink;
}
