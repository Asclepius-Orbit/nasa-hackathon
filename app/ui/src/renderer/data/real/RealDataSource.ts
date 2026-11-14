import type {
  AddExerciseResult,
  ExerciseEntry,
  AsclepiusApi,
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
} from '../../../shared/api';
import type { DataSource } from '../DataSource';
import { CATALOG, ODI, REPORT_FORM } from '../form-data';
import type {
  BaselineData,
  ContactEntry,
  CordHistoryData,
  CordProtocolsData,
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
import { toBaseline, toContactEntry, toCordHistory, toCordProtocols, toFirstDay, toGroundLink, toHistory, toHouston, toEndedRepState, toLostRepState, toRepState, toSession, toStrip, toToday } from './map-views';

/** The contract red-flag codes; the other urgent choices travel as `other_symptoms`. */
const RED_FLAG_CODES: ReadonlySet<string> = new Set(CATALOG.flags.map((flag) => flag.code));

/** An action the backend did not take: callers treat any rejection as "not recorded". The message is why. */
function refused(result: { ok: false; reason: string }): Promise<never> {
  return Promise.reject(new Error(result.reason));
}

/**
 * DataSource on the backend views (`window.asclepius.views`). A view that cannot be had rejects with the bridge error
 * (`locked`, `not_found`, `unavailable`): nothing is cached and nothing is invented.
 *
 * Writes go through the bridge and only an `ok` result counts: a stop, an urgent report or a logged call that the backend
 * did not take rejects, so the caller says "not recorded" and nothing is invented. The report form, the symptom catalogue
 * and the Oswestry items are static choices of the UI, not crew data, so they are served from `form-data`.
 *
 * It remembers (in memory only) the last session it started or showed and the pain stop it sent, so that a session that
 * Today no longer lists as in progress (ended, rejected, failed) still shows, and the Stopped screen knows its rep.
 */
export class RealDataSource implements DataSource {
  constructor(
    private readonly views: AsclepiusApi['views'],
    private readonly reps: AsclepiusApi['reps'],
    private readonly account: Pick<AsclepiusApi, 'profile' | 'dailyContext' | 'devices' | 'sessions' | 'reports' | 'urgent' | 'contacts' | 'houstonReport'>,
  ) {}

  private lastSessionId: string | null = null;
  private painStop: { sessionId: string; rep: number | null } | null = null;

  async getStrip(): Promise<StripData> {
    return toStrip(await this.views.strip());
  }

  async getToday(): Promise<TodayData> {
    return toToday(await this.views.today());
  }

  async getFirstDay(): Promise<FirstDayData> {
    return toFirstDay(await this.views.firstDay());
  }

  async getCordProtocols(): Promise<CordProtocolsData> {
    return toCordProtocols(await this.views.cordProtocols());
  }

  async getCordHistory(): Promise<CordHistoryData> {
    return toCordHistory(await this.views.cordHistory());
  }

  async getHistory(): Promise<HistoryData> {
    return toHistory(await this.views.history());
  }

  async getBaseline(): Promise<BaselineData> {
    return toBaseline(await this.views.baseline());
  }

  /** The session Today reports in progress, else the last one started or shown; `null` when there is none. */
  async getSession(): Promise<SessionData | null> {
    const running = toToday(await this.views.today()).sessionInProgress;
    const sessionId = running?.sessionId ?? this.lastSessionId;
    if (sessionId === null) return null;
    try {
      const session = toSession(await this.views.session(sessionId));
      this.lastSessionId = sessionId;
      return session;
    } catch (error) {
      // The backend no longer knows the remembered session: there is none to show. Anything else is a failure to read.
      if (running === null && error instanceof Error && error.message === 'not_found') return null;
      throw error;
    }
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

  // --- Static choices of the UI: the Oswestry items. ---

  getOswestry(): Promise<OswestryData> {
    return Promise.resolve(ODI);
  }

  /** Where the session stopped (remembered from the pain stop sent), the ground link of the strip and the logged contacts. */
  async getStopped(): Promise<StoppedData> {
    const [strip, contacts] = await Promise.all([this.views.strip(), this.account.contacts.list()]);
    return {
      sessionId: this.painStop?.sessionId ?? null,
      rep: this.painStop?.rep ?? null,
      groundLink: toGroundLink(strip.ground_link),
      contacts: contacts.map(toContactEntry),
    };
  }

  // --- Sessions. ---

  async startSession(protocolId: ProtocolId): Promise<StartSessionResult> {
    const result = await this.account.sessions.start({ protocolId });
    if (result.ok) this.lastSessionId = result.value.sessionId;
    return result;
  }

  async stopSessionForPain(sessionId: string, repIndex: number | null): Promise<void> {
    const result = await this.account.sessions.stop({ sessionId, reason: 'aborted_pain', repIndex });
    if (!result.ok) return refused(result);
    this.lastSessionId = sessionId;
    this.painStop = { sessionId, rep: repIndex };
    return undefined;
  }

  finishSession(sessionId: string): Promise<WriteResult<CommandSent>> {
    return this.account.sessions.stop({ sessionId, reason: 'completed' });
  }

  markArtifact(sessionId: string, repIndex: number | null): Promise<WriteResult<CommandSent>> {
    return this.account.sessions.markArtifact({ sessionId, repIndex });
  }

  calibrateMvc(sessionId: string): Promise<WriteResult<CommandSent>> {
    return this.account.sessions.calibrateMvc({ sessionId });
  }

  reprocessSession(sessionId: string): Promise<WriteResult<{ sessionId: string }>> {
    return this.account.sessions.reprocess({ sessionId });
  }

  // --- Reports, urgent report and the officer call. ---

  saveDailyReport(fields: DailyReportFields): Promise<WriteResult<ReportSaved>> {
    return this.account.reports.saveDaily(fields);
  }

  saveOswestry(answers: readonly OdiAnswer[]): Promise<WriteResult<ReportSaved>> {
    return this.account.reports.saveOswestry({ items: [...answers] });
  }

  addExercise(entry: ExerciseEntry): Promise<AddExerciseResult> {
    return this.account.reports.addExercise(entry);
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

  readonly sendsUrgentReports = true;

  async sendUrgentReport(crewId: string, codes: readonly string[]): Promise<void> {
    const result = await this.account.urgent.send({
      crewId,
      red_flags: codes.filter((code) => RED_FLAG_CODES.has(code)),
      other_symptoms: codes.filter((code) => !RED_FLAG_CODES.has(code)),
    });
    if (!result.ok) return refused(result);
    return undefined;
  }

  readonly generatesHoustonReport = true;

  async generateHoustonReport(): Promise<HoustonReportResult> {
    try {
      return await this.account.houstonReport.generate();
    } catch {
      // The bridge itself failing is the same as the backend not answering: nothing was saved.
      return { status: 'error', reason: 'unavailable' };
    }
  }

  async logCrewMedicalOfficerCall(sessionId: string | null): Promise<ContactEntry> {
    const result = await this.account.contacts.logOfficerCall({ sessionId });
    return result.ok ? toContactEntry(result.value) : refused(result);
  }
}
