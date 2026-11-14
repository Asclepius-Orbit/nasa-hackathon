import type {
  AddExerciseResult,
  ExerciseEntry,
  AstronautProfile,
  CommandSent,
  DailyContext,
  DailyContextFields,
  DailyReportFields,
  DeviceStatus,
  HoustonReportResult,
  OdiAnswer,
  ProfileFields,
  ProtocolId,
  ReportSaved,
  StartSessionResult,
  WriteResult,
} from '../../shared/api';
import type {
  BaselineData,
  ContactEntry,
  CordHistoryData,
  CordProtocolsData,
  FirstDayData,
  HistoryData,
  HoustonData,
  OswestryData,
  ReportFormData,
  RepsUpdate,
  SessionData,
  StoppedData,
  StripData,
  SymptomCatalog,
  TodayData,
} from './types';

/**
 * Everything the screens show about the crew member's health and the mission, as typed view-models.
 * The mock implements it today; a real one will sit on the bridge later. No-data is explicit (`null` / `nodata`), never a default.
 */
export interface DataSource {
  getStrip(): Promise<StripData>;
  getToday(): Promise<TodayData>;
  getFirstDay(): Promise<FirstDayData>;
  /** The two cord-resistance protocols: what is due, the last valid measurement of each and the calculated estimate. */
  getCordProtocols(): Promise<CordProtocolsData>;
  /** The latest cord measurements of each posture, valid and invalid; measurements only, never the estimate. */
  getCordHistory(): Promise<CordHistoryData>;
  getHistory(): Promise<HistoryData>;
  getReportForm(): Promise<ReportFormData>;
  /**
   * The session in progress with its protocol facts, or `null` when there is none (never an invented one). The real source
   * also follows the last session it started or showed, so an ended, rejected or failed one stays visible.
   */
  getSession(): Promise<SessionData | null>;
  getStopped(): Promise<StoppedData>;
  getBaseline(): Promise<BaselineData>;
  getOswestry(): Promise<OswestryData>;
  getHouston(): Promise<HoustonData>;
  getCatalog(): Promise<SymptomCatalog>;
  /**
   * Live rep counter of session `sessionId` (the caller already has it from `getSession` or Today). Calls `cb` with each
   * update; returns unsubscribe. The mock ignores the id; the real source never answers with simulated data.
   */
  subscribeReps(sessionId: string, cb: (update: RepsUpdate) => void): () => void;
  /** Starts a session of `protocolId`. `no_harness` (nothing sent) when the unlocked member has no paired harness; a refusal is a result. */
  startSession(protocolId: ProtocolId): Promise<StartSessionResult>;
  /**
   * Stops the session for pain at `repIndex` (from 1; `null` when the live counter does not know it). Rejects when it was not
   * recorded: the caller must then say so and never behave as if the stop happened.
   */
  stopSessionForPain(sessionId: string, repIndex: number | null): Promise<void>;
  /** Ends the session as completed (stop reason `completed`). */
  finishSession(sessionId: string): Promise<WriteResult<CommandSent>>;
  /** Marks an artefact at `repIndex` (`null` when the counter does not know it). */
  markArtifact(sessionId: string, repIndex: number | null): Promise<WriteResult<CommandSent>>;
  /** Asks the harness for the EMG maximum-effort calibration. */
  calibrateMvc(sessionId: string): Promise<WriteResult<CommandSent>>;
  /** Analyses again a session whose processing failed (`conflict` for any other state). */
  reprocessSession(sessionId: string): Promise<WriteResult<{ sessionId: string }>>;
  /** Stores today's daily report; only `ok: true` means saved. */
  saveDailyReport(fields: DailyReportFields): Promise<WriteResult<ReportSaved>>;
  /** Stores the weekly Oswestry answers (`score` = option index, 0-5); only `ok: true` means saved. */
  saveOswestry(answers: readonly OdiAnswer[]): Promise<WriteResult<ReportSaved>>;
  addExercise(entry: ExerciseEntry): Promise<AddExerciseResult>;
  /** True when `sendUrgentReport` really records and delivers; the Urgent dialog may then promise queueing. */
  readonly sendsUrgentReports: boolean;
  /** Sends the urgent symptom report for `crewId`. Rejects when it was not recorded: the caller must then say so and never show it as queued or sent. */
  sendUrgentReport(crewId: string, codes: readonly string[]): Promise<void>;
  /** Logs a call to the crew medical officer, with the session it relates to if any (shows in the Stop screen). */
  logCrewMedicalOfficerCall(sessionId: string | null): Promise<ContactEntry>;
  /** True when `generateHoustonReport` really makes the PDF; the demo cannot, and Today then says so instead of faking a file. */
  readonly generatesHoustonReport: boolean;
  /**
   * Makes the weekly PDF for Houston (last 7 days), asks where to save it, writes it and opens it. Never rejects: the
   * outcome is `saved`, `cancelled` or `error`. It is a separate deliverable and does not touch the daily summary.
   */
  generateHoustonReport(): Promise<HoustonReportResult>;
  /** The stored profile, or `null` when none is stored yet. Rejects with the bridge error (`locked`, `not_found`, `unavailable`). */
  getProfile(): Promise<AstronautProfile | null>;
  /** Stores the edited part of the profile. Only `ok: true` means stored; a refusal comes back as a result, not a rejection. */
  saveProfile(fields: ProfileFields): Promise<WriteResult<AstronautProfile>>;
  /** Today's mission context, or `null` when none is stored yet. */
  getTodayContext(): Promise<DailyContext | null>;
  saveTodayContext(fields: DailyContextFields): Promise<WriteResult<DailyContext>>;
  /** Harnesses that published a status for the signed-in astronaut (the ones that can be paired). */
  listDevices(): Promise<DeviceStatus[]>;
}
