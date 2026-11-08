import { realScheduler, type Scheduler } from '../../clock/scheduler';
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
  RepState,
  SessionData,
  StoppedData,
  StripData,
  SymptomCatalog,
  TodayData,
} from '../types';
import { BASELINE, buildDays, FIRST_DAY, ODI, SESSION, todayData } from './mock-data';

const REP_TICK_MS = 450;
const CYCLE_TICKS = 10;
const MISSION_DAY = 87;
const MS_PER_DAY = 86_400_000;
const MS_PER_MINUTE = 60_000;
const OFFICER_CALL_GMT = '13:52';

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
  private readonly listeners = new Set<(state: RepState) => void>();
  private stopTimer: (() => void) | null = null;
  private tick = 0;
  private reps: RepState = { rep: 4, total: 10, phase: 'pulling', barPercent: 60, last: { rep: 3, seconds: 2.3 } };
  private stoppedAtRep: number | null = null;
  private contacts: ContactEntry[] = [];

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
    return Promise.resolve(SESSION);
  }

  getStopped(): Promise<StoppedData> {
    return Promise.resolve({
      rep: this.stoppedAtRep ?? this.reps.rep,
      groundLink: { state: 'down', backInMin: 12 },
      crewMedicalOfficerOnBoard: true,
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

  getHouston(): Promise<HoustonData> {
    const last = this.contacts.at(-1);
    return Promise.resolve({
      crewCode: 'AST-01',
      day: MISSION_DAY,
      level: 'amber',
      confidencePct: 70,
      dataAvailable: 8,
      dataTotal: 10,
      drivers: 'Lumbar length +2.1 mm, curve flatter, pain 7-day mean 4',
      warningSymptoms: 'None reported',
      sessions: this.stoppedAtRep === null ? '1 in progress' : `1 stopped for pain at rep ${String(this.stoppedAtRep)}`,
      contacts: last ? `Crew medical officer called at ${last.atGmt}` : 'None today',
      plan: ['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01'],
      outbox: [
        { label: 'Day 87 summary', state: 'queued', receivedGmt: null },
        { label: 'Day 86 summary', state: 'sent', receivedGmt: '21:04' },
        { label: 'Day 85 summary', state: 'sent', receivedGmt: '20:51' },
      ],
      groundLink: { state: 'down', backInMin: 12 },
    });
  }

  stopSessionForPain(): Promise<void> {
    this.stoppedAtRep = this.reps.rep;
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

  subscribeReps(cb: (state: RepState) => void): () => void {
    this.listeners.add(cb);
    cb(this.reps);
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
    if (this.tick === 0 && rep < this.reps.total) {
      last = { rep, seconds: 2.4 };
      rep += 1;
    }
    this.reps = {
      rep,
      total: this.reps.total,
      phase: pulling ? 'pulling' : 'letting_up',
      barPercent: pulling ? (this.tick + 1) * 20 : (CYCLE_TICKS - this.tick) * 20,
      last,
    };
    this.listeners.forEach((listener) => {
      listener(this.reps);
    });
  }
}
