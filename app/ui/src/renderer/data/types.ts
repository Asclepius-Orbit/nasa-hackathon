import type { Assessment, DailySelfReport, HistoryDaySession, StripView, TaskCode as ContractTaskCode, TodayView } from '../../contracts/v1';

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

/** Flight phase code (contract `mission_phase`); the words live in strings/codes. */
export type MissionPhase = NonNullable<StripView['mission']['phase']>;
export type TaskCode = ContractTaskCode;
export type SessionState = HistoryDaySession['state'];
export type MissingReason = NonNullable<TodayView['trust']>['missing'][number]['reason'];

/** A stream that is not sending (device status stalled or disabled). */
export interface WeakStream {
  stream: string;
  state: 'stalled' | 'disabled';
}

export interface HarnessStatus {
  id: string;
  /** `null` when the device did not report it. */
  batteryPct: number | null;
  /** Streams that are not sending; empty when every stream is active. */
  weakStreams: readonly WeakStream[];
  degraded: boolean;
}

/** Facts shown in the always-visible strip and the offline banner. Every unknown is `null`, never a default. */
export interface StripData {
  /** `null` without a launch date. */
  missionDay: number | null;
  /** Launch date (`YYYY-MM-DD`); MET is measured from here. `null` when unknown. */
  missionStart: string | null;
  flightPhase: MissionPhase | null;
  /** `null` until a harness is paired. */
  harness: HarnessStatus | null;
  /** `null` = unknown (the backend does not model the ground link yet): never shown as up. */
  groundLink: GroundLink | null;
  /** `null` = unknown. */
  lastContactMinAgo: number | null;
  /** Reports still due today (daily and weekly Oswestry); the badge on the Report tab (0 hides it). */
  reportsDue: number;
}

/** One driver of the level, as codes and numbers; the words come from strings/codes. */
export interface Reason {
  featureId: string;
  deviationZ: number | null;
  contribution: number | null;
}

export interface TrustFacts {
  confidencePct: number;
  dataAvailable: number;
  dataTotal: number;
  missing: readonly { what: string; reason: MissingReason }[];
  /** ISO time the assessment was made. */
  assessedAt: string;
}

export interface TodayData {
  /** `nodata` when there is no assessment: every assessment field is then empty or `null`. */
  level: DayLevel;
  /** Recommendation codes (`REC_*`) of the assessment, in order. */
  recommendations: readonly string[];
  reasons: readonly Reason[];
  trust: TrustFacts | null;
  /** Other things still to do today. */
  alsoToday: readonly TaskCode[];
  /** Lumbar length change vs the astronaut reference; `null` = no data (never 0). */
  lumbarLengthMm: number | null;
  /** A session in progress, if any (live rep count comes from `subscribeReps`). */
  sessionInProgress: { sessionId: string; protocolId: string; startedAt: string; totalReps: number | null } | null;
}

export interface BaselineSession {
  /** `null` for a planned session. */
  sessionId: string | null;
  protocolId: string;
  status: 'complete' | 'planned';
  /** Days relative to launch: L-30 is -30. */
  offsetDays: number;
  /** `null` when it cannot be derived. */
  date: string | null;
}

export interface BaselineData {
  recorded: number;
  required: number;
  /** `null` while no reference was built. */
  referenceVersion: number | null;
  builtFromSessions: number;
  sessions: readonly BaselineSession[];
}

export interface FirstDayData {
  crewFirstName: string;
  /** Tasks still to do, in the order to show them. */
  todo: readonly TaskCode[];
  baseline: BaselineData;
}

export interface DayRecord {
  /** Calendar date, `YYYY-MM-DD`: the key of a day. */
  date: string;
  /** Mission day; `null` without a launch date. */
  day: number | null;
  level: DayLevel;
  /** Lumbar length vs reference in mm; `null` = no data (never 0). */
  lengthMm: number | null;
  /** Daily-report pain 0-10; `null` = no report. */
  pain: number | null;
  session: { sessionId: string; state: SessionState; protocolId: string } | null;
  report: { kind: 'daily' | 'weekly_odi' | 'red_flag'; redFlags: readonly RedFlagCode[] } | null;
}

export interface HistoryData {
  from: string;
  to: string;
  /** Mission day of today; `null` without a launch date. */
  todayDay: number | null;
  /** The astronaut own preflight range for lumbar length, in mm; `null` without a reference. */
  preflightRangeMm: { lo: number; hi: number } | null;
  days: readonly DayRecord[];
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
  status: StepStatus;
}

export interface SensorCheck {
  stream: string;
  status: 'good' | 'weak';
  state: 'active' | 'stalled' | 'disabled';
}

export interface SessionData {
  sessionId: string;
  state: SessionState;
  protocolId: string;
  /** `null` when the protocol uses no band. */
  band: { color: string; nominalKg: number } | null;
  /** `null` when the protocol has no repetitions. */
  totalReps: number | null;
  /** `null` when the protocol has no rest. */
  restSeconds: number | null;
  fitItems: readonly string[];
  /** `null` before the gateway started the session. */
  startedAt: string | null;
  steps: readonly SessionStep[];
  sensorChecks: readonly SensorCheck[];
  /** `null` when no calibrate command was sent. */
  calibration: { commandId: string; acked: boolean; ackedAt: string | null } | null;
  /** Reserved by the backend: `null` for now. */
  stretchedCm: number | null;
  /** Reserved by the backend: `null` for now. */
  plannedAt: string | null;
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
