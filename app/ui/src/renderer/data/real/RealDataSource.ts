import type { AsclepiusApi, AstronautProfile, DailyContext, DailyContextFields, DeviceStatus, ProfileFields, WriteResult } from '../../../shared/api';
import type { DataSource } from '../DataSource';
import { CATALOG, REPORT_FORM } from '../form-data';
import type {
  BaselineData,
  ContactEntry,
  FirstDayData,
  HistoryData,
  HoustonData,
  OswestryData,
  RepsUpdate,
  RepState,
  ReportFormData,
  SessionData,
  StoppedData,
  StripData,
  SymptomCatalog,
  TodayData,
} from '../types';
import { toBaseline, toFirstDay, toHistory, toHouston, toEndedRepState, toLostRepState, toRepState, toSession, toStrip, toToday } from './map-views';

/** Rejection of a view whose backend does not exist yet: `not_built`, so the screen says so instead of "no connection". */
const notBuilt = (): Promise<never> => Promise.reject(new Error('not_built'));
/** Rejection of an action that cannot be recorded: callers treat any rejection as "not recorded". */
const unavailable = (): Promise<never> => Promise.reject(new Error('unavailable'));

/**
 * DataSource on the backend views (`window.asclepius.views`). A view that cannot be had rejects with the bridge error
 * (`locked`, `not_found`, `unavailable`): nothing is cached and nothing is invented.
 *
 * The Stop screen, Oswestry, stop for pain and the call log are NOT on the backend yet (issue #63). The two views
 * reject `not_built` and the two actions `unavailable`, rather than answer with the mock: no invented health data or false "recorded"
 * in real mode. Each of those issues replaces its methods here. The report form and the symptom catalogue are static
 * choices of the UI, not crew data, so they are served from `form-data`.
 */
export class RealDataSource implements DataSource {
  constructor(
    private readonly views: AsclepiusApi['views'],
    private readonly reps: AsclepiusApi['reps'],
    private readonly account: Pick<AsclepiusApi, 'profile' | 'dailyContext' | 'devices'>,
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

  // --- Profile, today's context and devices: straight through the bridge. ---

  getProfile(): Promise<AstronautProfile | null> {
    return this.account.profile.get();
  }

  saveProfile(fields: ProfileFields): Promise<WriteResult<AstronautProfile>> {
    return this.account.profile.update(fields);
  }

  getTodayContext(): Promise<DailyContext | null> {
    return this.account.dailyContext.today();
  }

  saveTodayContext(fields: DailyContextFields): Promise<WriteResult<DailyContext>> {
    return this.account.dailyContext.updateToday(fields);
  }

  listDevices(): Promise<DeviceStatus[]> {
    return this.account.devices.list();
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

  /** The summary and the link from `/houston`, the queue from `/outbox`; if either fails the screen fails, never half of it. */
  async getHouston(): Promise<HoustonData> {
    const [houston, outbox] = await Promise.all([this.views.houston(), this.views.outbox()]);
    return toHouston(houston, outbox);
  }

  /**
   * The live counter of `sessionId` from the backend stream (`api.reps`), mapped in `map-views`. `lost` keeps the last
   * known counter flagged as not live (or says `reconnecting` when there is none yet); `end` delivers the final count flagged
   * `ended` and stops listening; an `error` is final and is
   * reported as `none` (no reps / unknown session), `unavailable` or `locked`. The mock never stands in here.
   */
  subscribeReps(sessionId: string, cb: (update: RepsUpdate) => void): () => void {
    let last: RepState | null = null;
    let stopStream: (() => void) | null = null;
    let done = false as boolean; // widened: a synchronous event can set it before `subscribe` returns
    const stop = (): void => {
      done = true;
      stopStream?.();
    };
    stopStream = this.reps.subscribe(sessionId, (event) => {
      if (done) return;
      switch (event.kind) {
        case 'state':
          last = toRepState(event.state);
          cb({ kind: 'state', state: last });
          return;
        case 'end':
          cb({ kind: 'state', state: toEndedRepState(event.state) });
          stop();
          return;
        case 'lost':
          last = toLostRepState(last);
          cb(last === null ? { kind: 'reconnecting' } : { kind: 'state', state: last });
          return;
        case 'error':
          cb({ kind: event.code === 'unavailable' ? 'unavailable' : event.code === 'locked' ? 'locked' : 'none' });
          stop();
      }
    });
    if (done) stopStream();
    return stop;
  }

  /** Not recorded anywhere yet: the caller must say so and must not behave as if the stop was saved. */
  stopSessionForPain(): Promise<void> {
    return unavailable();
  }

  readonly sendsUrgentReports = false;

  /** There is no outbox for urgent reports yet (#63): `not_built`, so nothing is shown as saved, queued or sent. */
  sendUrgentReport(): Promise<void> {
    return notBuilt();
  }

  logCrewMedicalOfficerCall(): Promise<ContactEntry> {
    return unavailable();
  }
}
