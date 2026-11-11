import type {
  AstronautProfile,
  CommandSent,
  DailyContext,
  DailyContextFields,
  DeviceStatus,
  ProfileFields,
  ProtocolId,
  ReportSaved,
  StartSessionResult,
  WriteResult,
} from '../../../shared/api';
import { realScheduler, type Scheduler } from '../../clock/scheduler';
import type { DataSource } from '../DataSource';
import { CATALOG, ODI, REPORT_FORM } from '../form-data';
import type {
  BaselineData,
  ContactEntry,
  FirstDayData,
  HistoryData,
  HoustonData,
  OswestryData,
  ReportFormData,
  RepsUpdate,
  RepState,
  SessionData,
  StoppedData,
  StripData,
  SymptomCatalog,
  TodayData,
} from '../types';
import { BASELINE, buildDays, FIRST_DAY, SESSION, todayData } from './mock-data';

const REP_TICK_MS = 450;
const CYCLE_TICKS = 10;
const MISSION_DAY = 87;
const MS_PER_DAY = 86_400_000;
const MS_PER_MINUTE = 60_000;
const OFFICER_CALL_GMT = '13:52';
const MOCK_STARTED_ID = 'mock-started';

export interface MockDataSourceOptions {
  /** Current time; the mission start is derived from it so today is always mission day 87. */
  now?: () => Date;
  /** Drives the simulated rep counter (450 ms interval). */
  scheduler?: Scheduler;
}

/** DataSource backed by the mock values; the live rep counter is simulated. */
export class MockDataSource implements DataSource {
  private readonly now: () => Date;
  private readonly scheduler: Scheduler;
  private readonly listeners = new Set<(update: RepsUpdate) => void>();
  private stopTimer: (() => void) | null = null;
  private tick = 0;
  private reps: RepState = { rep: 4, total: 10, phase: 'pulling', barPercent: 60, last: { rep: 3, seconds: 2.3 }, signal: 'ok' };
  private stopped: { sessionId: string; rep: number | null } | null = null;
  private session: SessionData | null = SESSION;
  private contacts: ContactEntry[] = [];
  private profile: AstronautProfile | null = null;
  private context: DailyContext | null = null;

  constructor(options: MockDataSourceOptions = {}) {
    this.now = options.now ?? (() => new Date());
    this.scheduler = options.scheduler ?? realScheduler;
  }

  getStrip(): Promise<StripData> {
    const today = this.now();
    const midnight = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    return Promise.resolve({
      missionDay: MISSION_DAY,
      missionStart: new Date(midnight - (MISSION_DAY - 1) * MS_PER_DAY).toISOString().slice(0, 10),
      flightPhase: 'inflight',
      harness: { id: 'wb-01', batteryPct: 64, weakStreams: [{ stream: 'emg_lat_right', state: 'stalled' }], degraded: false, stale: false, lastSeenAt: new Date(today.getTime() - MS_PER_MINUTE).toISOString() },
      groundLink: { state: 'down', backInMin: 12 },
      lastContactMinAgo: 4,
      reportsDue: 1,
    });
  }

  getProfile(): Promise<AstronautProfile | null> {
    return Promise.resolve(this.profile);
  }

  saveProfile(fields: ProfileFields): Promise<WriteResult<AstronautProfile>> {
    const { height_standing_cm, height_sitting_cm, body_mass_kg, ...rest } = fields;
    const value: AstronautProfile = {
      schema_version: '1.0.0',
      astronaut_id: 'AST-01',
      updated_at: this.now().toISOString(),
      ...rest,
      anthropometry: { height_standing_cm, body_mass_kg, ...(height_sitting_cm === null ? {} : { height_sitting_cm }) },
    };
    this.profile = value;
    return Promise.resolve({ ok: true, value });
  }

  getTodayContext(): Promise<DailyContext | null> {
    return Promise.resolve(this.context);
  }

  saveTodayContext(fields: DailyContextFields): Promise<WriteResult<DailyContext>> {
    const { mission_day, ...rest } = fields;
    const value: DailyContext = {
      schema_version: '1.0.0',
      astronaut_id: 'AST-01',
      date: this.now().toISOString().slice(0, 10),
      ...rest,
      ...(mission_day === null ? {} : { mission_day }),
    };
    this.context = value;
    return Promise.resolve({ ok: true, value });
  }

  listDevices(): Promise<DeviceStatus[]> {
    const at = this.now().getTime();
    const device = (id: string, status: DeviceStatus['status'], battery: number, minutesAgo: number): DeviceStatus => ({
      schema_version: '1.0.0',
      astronaut_id: 'AST-01',
      device_id: id,
      timestamp: new Date(at - minutesAgo * MS_PER_MINUTE).toISOString(),
      status,
      source: 'device',
      battery_pct: battery,
      link: { type: 'ble', rssi_dbm: -62 },
    });
    return Promise.resolve([device('wb-01', 'degraded', 64, 1), device('wb-02', 'ok', 91, 3)]);
  }

  getToday(): Promise<TodayData> {
    return Promise.resolve(todayData(this.now()));
  }

  getFirstDay(): Promise<FirstDayData> {
    return Promise.resolve(FIRST_DAY);
  }

  getHistory(): Promise<HistoryData> {
    const days = buildDays(this.now());
    return Promise.resolve({
      from: days[0]?.date ?? '',
      to: days.at(-1)?.date ?? '',
      days,
      todayDay: MISSION_DAY,
      preflightRangeMm: { lo: -1, hi: 1 },
    });
  }

  getReportForm(): Promise<ReportFormData> {
    return Promise.resolve(REPORT_FORM);
  }

  getSession(): Promise<SessionData | null> {
    return Promise.resolve(this.session);
  }

  startSession(protocolId: ProtocolId): Promise<StartSessionResult> {
    const counted = protocolId === 'lat_pulldown_standing_v1';
    this.stopped = null;
    this.session = {
      ...SESSION,
      sessionId: MOCK_STARTED_ID,
      state: 'starting',
      protocolId,
      band: counted ? SESSION.band : null,
      totalReps: counted ? SESSION.totalReps : null,
      restSeconds: counted ? SESSION.restSeconds : null,
      startedAt: null,
      steps: SESSION.steps.map((step, index) => ({ id: step.id, status: index === 0 ? 'now' : 'todo' })),
      sensorChecks: [],
      calibration: null,
    };
    return Promise.resolve({ ok: true, value: { sessionId: MOCK_STARTED_ID, state: 'starting' } });
  }

  finishSession(): Promise<WriteResult<CommandSent>> {
    this.session = null;
    return Promise.resolve({ ok: true, value: { commandId: 'mock-command' } });
  }

  markArtifact(): Promise<WriteResult<CommandSent>> {
    return Promise.resolve({ ok: true, value: { commandId: 'mock-command' } });
  }

  calibrateMvc(): Promise<WriteResult<CommandSent>> {
    return Promise.resolve({ ok: true, value: { commandId: 'mock-command' } });
  }

  reprocessSession(sessionId: string): Promise<WriteResult<{ sessionId: string }>> {
    return Promise.resolve({ ok: true, value: { sessionId } });
  }

  saveDailyReport(): Promise<WriteResult<ReportSaved>> {
    return Promise.resolve({ ok: true, value: { reportId: 'mock-report', assessmentLevel: null, assessmentSkipped: 'mock' } });
  }

  saveOswestry(): Promise<WriteResult<ReportSaved>> {
    return Promise.resolve({ ok: true, value: { reportId: 'mock-report', assessmentLevel: null, assessmentSkipped: 'mock' } });
  }

  getStopped(): Promise<StoppedData> {
    return Promise.resolve({
      sessionId: this.stopped?.sessionId ?? SESSION.sessionId,
      // Opened from the mock menu without a stop: the counter's rep, as the mock's live counter shows it.
      rep: this.stopped === null ? this.reps.rep : this.stopped.rep,
      groundLink: { state: 'down', backInMin: 12 },
      contacts: [...this.contacts],
    });
  }

  getBaseline(): Promise<BaselineData> {
    return Promise.resolve(BASELINE);
  }

  getOswestry(): Promise<OswestryData> {
    return Promise.resolve(ODI);
  }

  getCatalog(): Promise<SymptomCatalog> {
    return Promise.resolve(CATALOG);
  }

  /** The mock day 87 as the contract would give it: codes and nulls, simulated transport, content not validated. */
  getHouston(): Promise<HoustonData> {
    const day = (back: number): string => new Date(this.now().getTime() - back * MS_PER_DAY).toISOString().slice(0, 10);
    const at = (back: number, hhmm: string): string => `${day(back)}T${hhmm}:00.000Z`;
    return Promise.resolve({
      crewCode: 'AST-01',
      summary: {
        date: day(0),
        missionDay: MISSION_DAY,
        revision: 1,
        asOf: at(0, '09:00'),
        level: 'amber',
        confidencePct: 70,
        dataAvailable: 8,
        dataTotal: 10,
        drivers: [
          { featureId: 'stretch.lumbar.peak_elongation_mm', deviationZ: 2.1 },
          { featureId: 'imu.l3.sagittal.rom_deg', deviationZ: -1.2 },
          { featureId: 'symptoms.pain_nrs_7d_mean', deviationZ: 1.8 },
        ],
        redFlags: [],
        sessions: [{ sessionId: 'mock-session', state: this.stopped === null ? 'running' : 'ended', protocolId: 'lat_pulldown_standing_v1' }],
        recommendations: ['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01'],
        escalation: { required: false, urgency: null, reasons: [] },
        contacts: null,
      },
      outbox: [
        { date: day(0), missionDay: MISSION_DAY, revision: 1, state: 'queued', enqueuedAt: at(0, '09:00'), receivedAt: null, attempts: 1 },
        { date: day(1), missionDay: MISSION_DAY - 1, revision: 1, state: 'sent', enqueuedAt: at(1, '21:03'), receivedAt: at(1, '21:04'), attempts: 1 },
        { date: day(2), missionDay: MISSION_DAY - 2, revision: 1, state: 'sent', enqueuedAt: at(2, '20:50'), receivedAt: at(2, '20:51'), attempts: 1 },
      ],
      groundLink: { state: 'down', backInMin: 12 },
      lastContactAt: at(1, '21:04'),
      transport: 'simulated',
      contentValidated: false,
    });
  }

  stopSessionForPain(sessionId: string, repIndex: number | null): Promise<void> {
    this.stopped = { sessionId, rep: repIndex };
    this.session = null;
    return Promise.resolve();
  }

  readonly sendsUrgentReports = true;

  sendUrgentReport(): Promise<void> {
    return Promise.resolve();
  }

  logCrewMedicalOfficerCall(): Promise<ContactEntry> {
    const entry: ContactEntry = {
      atGmt: OFFICER_CALL_GMT,
      text: `${OFFICER_CALL_GMT} GMT: call to the crew medical officer logged. Included in the day ${String(MISSION_DAY)} report to Houston.`,
    };
    this.contacts.push(entry);
    return Promise.resolve(entry);
  }

  subscribeReps(_sessionId: string, cb: (update: RepsUpdate) => void): () => void {
    this.listeners.add(cb);
    cb({ kind: 'state', state: this.reps });
    this.stopTimer ??= this.scheduler.every(REP_TICK_MS, () => {
      this.advance();
    });
    return () => {
      this.listeners.delete(cb);
      if (this.listeners.size === 0 && this.stopTimer) {
        this.stopTimer();
        this.stopTimer = null;
      }
    };
  }

  /** One 450 ms step of the simulated IMU: pull for 5 steps, let up for 5, count a rep per cycle up to the total. */
  private advance(): void {
    this.tick = (this.tick + 1) % CYCLE_TICKS;
    const pulling = this.tick < CYCLE_TICKS / 2;
    let { rep, last } = this.reps;
    if (this.tick === 0 && this.reps.total !== null && rep < this.reps.total) {
      last = { rep, seconds: 2.4 };
      rep += 1;
    }
    this.reps = {
      rep,
      total: this.reps.total,
      phase: pulling ? 'pulling' : 'letting_up',
      barPercent: pulling ? (this.tick + 1) * 20 : (CYCLE_TICKS - this.tick) * 20,
      last,
      signal: 'ok',
    };
    this.listeners.forEach((listener) => {
      listener({ kind: 'state', state: this.reps });
    });
  }
}
