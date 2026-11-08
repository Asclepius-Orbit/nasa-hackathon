import type { AsclepiusApi } from '../../../shared/api';
import type { DataSource } from '../DataSource';
import { MockDataSource } from '../mock/MockDataSource';
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

/**
 * DataSource on the backend views (`window.asclepius.views`). A view that cannot be had rejects with the bridge error
 * (`locked`, `not_found`, `unavailable`): nothing is cached and nothing is invented.
 *
 * The rest of the interface (report form, Stop screen, Oswestry, Houston summary, catalogue, live reps, stop for pain,
 * call log) is NOT on the backend yet. It delegates to MockDataSource until issues #61 to #63 land; each of those
 * issues replaces its methods here and this delegation shrinks.
 */
export class RealDataSource implements DataSource {
  constructor(
    private readonly views: AsclepiusApi['views'],
    private readonly pending: DataSource = new MockDataSource(),
  ) {}

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

  // --- Not on the backend yet (#61 to #63): the mock answers. ---

  getReportForm(): Promise<ReportFormData> {
    return this.pending.getReportForm();
  }

  getStopped(): Promise<StoppedData> {
    return this.pending.getStopped();
  }

  getOswestry(): Promise<OswestryData> {
    return this.pending.getOswestry();
  }

  getHouston(): Promise<HoustonData> {
    return this.pending.getHouston();
  }

  getCatalog(): Promise<SymptomCatalog> {
    return this.pending.getCatalog();
  }

  /**
   * The backend has no live rep signal yet (issue #61), so this emits nothing: the mock's simulated counter must never
   * reach the crew in real mode. The screens show "no live rep count" while no state arrives.
   */
  subscribeReps(): () => void {
    return () => undefined;
  }

  stopSessionForPain(): Promise<void> {
    return this.pending.stopSessionForPain();
  }

  logCrewMedicalOfficerCall(): Promise<ContactEntry> {
    return this.pending.logCrewMedicalOfficerCall();
  }
}
