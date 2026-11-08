import type {
  BaselineData,
  ContactEntry,
  FirstDayData,
  HistoryData,
  HoustonData,
  OswestryData,
  ReportFormData,
  RepState,
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
  /** Live rep counter. Calls `cb` at once with the current state, then on every change; returns unsubscribe. */
  subscribeReps(cb: (state: RepState) => void): () => void;
  /** Records that the session was stopped for pain at the current rep (shows in the Houston summary). */
  stopSessionForPain(): Promise<void>;
  /** Logs a call to the crew medical officer (shows in the Stop screen and the Houston summary). */
  logCrewMedicalOfficerCall(): Promise<ContactEntry>;
}
