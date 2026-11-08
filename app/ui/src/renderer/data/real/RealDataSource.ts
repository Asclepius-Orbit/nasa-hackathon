import type { AsclepiusApi } from '../../../shared/api';
import type { DataSource } from '../DataSource';
import { CATALOG, REPORT_FORM } from '../form-data';
import type {
  BaselineData,
  ContactEntry,
  FirstDayData,
  HistoryData,
  HoustonData,
  OswestryData,
  ReportFormData,
  SessionData,
  StoppedData,
  StripData,
  SymptomCatalog,
  TodayData,
} from '../types';
import { toBaseline, toFirstDay, toHistory, toSession, toStrip, toToday } from './map-views';

/** Rejection of a view whose backend does not exist yet: `not_built`, so the screen says so instead of "no connection". */
const notBuilt = (): Promise<never> => Promise.reject(new Error('not_built'));
/** Rejection of an action that cannot be recorded: callers treat any rejection as "not recorded". */
const unavailable = (): Promise<never> => Promise.reject(new Error('unavailable'));

/**
 * DataSource on the backend views (`window.asclepius.views`). A view that cannot be had rejects with the bridge error
 * (`locked`, `not_found`, `unavailable`): nothing is cached and nothing is invented.
 *
 * The Stop screen, Oswestry, the Houston summary, stop for pain and the call log are NOT on the backend yet (issues
 * #61 to #63). The three views reject `not_built` and the two actions `unavailable`, rather than answer with the mock: no invented health data or false "recorded"
 * in real mode. Each of those issues replaces its methods here. The report form and the symptom catalogue are static
 * choices of the UI, not crew data, so they are served from `form-data`.
 */
export class RealDataSource implements DataSource {
  constructor(private readonly views: AsclepiusApi['views']) {}

  async getStrip(): Promise<StripData> {
    return toStrip(await this.views.strip());
  }

  async getToday(): Promise<TodayData> {
    return toToday(await this.views.today());
  }

  async getFirstDay(): Promise<FirstDayData> {
    return toFirstDay(await this.views.firstDay());
  }

  async getHistory(): Promise<HistoryData> {
    return toHistory(await this.views.history());
  }

  async getBaseline(): Promise<BaselineData> {
    return toBaseline(await this.views.baseline());
  }

  /** The session today reports in progress; `null` when there is none. */
  async getSession(): Promise<SessionData | null> {
    const running = toToday(await this.views.today()).sessionInProgress;
    if (running === null) return null;
    return toSession(await this.views.session(running.sessionId));
  }

  // --- Static choices of the forms (not crew data). ---

  getReportForm(): Promise<ReportFormData> {
    return Promise.resolve(REPORT_FORM);
  }

  getCatalog(): Promise<SymptomCatalog> {
    return Promise.resolve(CATALOG);
  }

  // --- Not on the backend yet (#61 to #63): not_built / unavailable, never the mock. ---

  getStopped(): Promise<StoppedData> {
    return notBuilt();
  }

  getOswestry(): Promise<OswestryData> {
    return notBuilt();
  }

  getHouston(): Promise<HoustonData> {
    return notBuilt();
  }

  /**
   * The backend has no live rep signal yet (issue #61), so this emits nothing: the mock's simulated counter must never
   * reach the crew in real mode. The screens show "no live rep count" while no state arrives.
   */
  subscribeReps(): () => void {
    return () => undefined;
  }

  /** Not recorded anywhere yet: the caller must say so and must not behave as if the stop was saved. */
  stopSessionForPain(): Promise<void> {
    return unavailable();
  }

  logCrewMedicalOfficerCall(): Promise<ContactEntry> {
    return unavailable();
  }
}
