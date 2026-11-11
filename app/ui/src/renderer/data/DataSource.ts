import type { AstronautProfile, DailyContext, DailyContextFields, DeviceStatus, ProfileFields, WriteResult } from '../../shared/api';
import type {
  BaselineData,
  ContactEntry,
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
  getHistory(): Promise<HistoryData>;
  getReportForm(): Promise<ReportFormData>;
  /** The session in progress with its protocol facts, or `null` when there is none (never an invented one). */
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
  /** Records that the session was stopped for pain at the current rep (shows in the Houston summary). */
  stopSessionForPain(): Promise<void>;
  /** True when `sendUrgentReport` really records and delivers. The real source cannot yet (#63), so the Urgent dialog must not promise anything. */
  readonly sendsUrgentReports: boolean;
  /** Sends the urgent symptom report. Rejects when it was not recorded: the caller must then say so and never show it as queued or sent. */
  sendUrgentReport(codes: readonly string[]): Promise<void>;
  /** Logs a call to the crew medical officer (shows in the Stop screen and the Houston summary). */
  logCrewMedicalOfficerCall(): Promise<ContactEntry>;
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
