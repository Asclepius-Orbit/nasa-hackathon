import type {
  AddExerciseResult,
  ExerciseEntry,
  AsclepiusApi,
  AstronautProfile,
  AuthSession,
  BaselineView,
  ChatEvent,
  CommandSent,
  CreateCrewResult,
  CrewMember,
  DailyReportFields,
  MedicalContactEntry,
  OdiAnswer,
  ReportSaved,
  ReportsDue,
  StartSessionResult,
  StopInput,
  UrgentInput,
  DailyContext,
  DailyContextFields,
  DeviceStatus,
  FirstDayResult,
  HistoryView,
  HoustonView,
  OutboxView,
  PinResult,
  ProfileFields,
  RepsEvent,
  SessionView,
  SetHarnessResult,
  Settings,
  StripView,
  TodayView,
  ViewRange,
  ViewsErrorCode,
  WriteResult,
} from '../../shared/api';

const LOCK_MS = 5 * 60_000;
const MAX_TRIES = 3;

export const DEFAULT_SETTINGS: Settings = {
  lengthUnit: 'mm',
  timeFormat: 'gmt',
  textSize: 'large',
  voiceCommands: true,
  footPedal: false,
};

export const DEFAULT_PROFILE: AstronautProfile = {
  schema_version: '1.0.0',
  astronaut_id: 'AST-01',
  updated_at: '2026-11-01T08:00:00Z',
  date_of_birth: '1988-04-12',
  sex_at_birth: 'female',
  launch_date: '2026-08-20',
  landing_date: null,
  anthropometry: { height_standing_cm: 171, height_sitting_cm: 90, body_mass_kg: 64.5 },
};

export const DEFAULT_CONTEXT: DailyContext = {
  schema_version: '1.0.0',
  astronaut_id: 'AST-01',
  date: '2026-11-05',
  mission_day: 78,
  phase: 'inflight',
  gravity_environment: 'microgravity',
};

/** Hand-written in-memory AsclepiusApi for tests. Configure the public fields, then inspect `calls`. */
export class FakeAsclepiusApi implements AsclepiusApi {
  crewMembers: CrewMember[] = [
    { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-01' },
    { id: 'AST-02', name: 'Marc Dubois', harnessId: 'wb-02' },
  ];
  pins = new Map<string, string>([
    ['AST-01', '1234'],
    ['AST-02', '1234'],
  ]);
  session: AuthSession | null = null;
  settingsValue: Settings = { ...DEFAULT_SETTINGS };
  /** Names of the methods called, in order, e.g. `settings.update`. */
  calls: string[] = [];
  private wrong = new Map<string, number>();
  /** Argument of the last `crew.setHarness`. */
  lastHarnessId: string | null | undefined;
  /** Makes `crew.setHarness` answer a failure. */
  harnessFailure: 'locked' | 'unavailable' | undefined;

  /** Stored profile (`null`: none yet). `profile.update` replaces it. */
  profileValue: AstronautProfile | null = DEFAULT_PROFILE;
  /** Today's stored context (`null`: none yet). */
  contextValue: DailyContext | null = DEFAULT_CONTEXT;
  devicesValue: DeviceStatus[] = [];
  /** Makes the matching read reject with this code. */
  readErrors: Partial<Record<'profile' | 'context' | 'devices', ViewsErrorCode>> = {};
  /** Forces the answer of `profile.update` / `dailyContext.updateToday` (default: stores and answers `ok`). */
  profileWrite: WriteResult<AstronautProfile> | undefined;
  contextWrite: WriteResult<DailyContext> | undefined;
  lastProfileFields: ProfileFields | undefined;
  lastContextFields: DailyContextFields | undefined;

  profile = {
    get: (): Promise<AstronautProfile | null> => {
      this.calls.push('profile.get');
      return this.read('profile', this.profileValue);
    },
    update: (fields: ProfileFields): Promise<WriteResult<AstronautProfile>> => {
      this.calls.push('profile.update');
      this.lastProfileFields = fields;
      if (this.profileWrite !== undefined) return Promise.resolve(this.profileWrite);
      const { height_standing_cm, height_sitting_cm, body_mass_kg, ...rest } = fields;
      const value: AstronautProfile = {
        ...DEFAULT_PROFILE,
        ...rest,
        anthropometry: { height_standing_cm, body_mass_kg, ...(height_sitting_cm === null ? {} : { height_sitting_cm }) },
      };
      this.profileValue = value;
      return Promise.resolve({ ok: true, value });
    },
  };

  dailyContext = {
    today: (): Promise<DailyContext | null> => {
      this.calls.push('dailyContext.today');
      return this.read('context', this.contextValue);
    },
    updateToday: (fields: DailyContextFields): Promise<WriteResult<DailyContext>> => {
      this.calls.push('dailyContext.updateToday');
      this.lastContextFields = fields;
      if (this.contextWrite !== undefined) return Promise.resolve(this.contextWrite);
      const { mission_day, ...rest } = fields;
      const value: DailyContext = { ...DEFAULT_CONTEXT, ...rest, ...(mission_day === null ? {} : { mission_day }) };
      this.contextValue = value;
      return Promise.resolve({ ok: true, value });
    },
  };

  devices = {
    list: (): Promise<DeviceStatus[]> => {
      this.calls.push('devices.list');
      return this.read('devices', [...this.devicesValue]);
    },
  };

  /** Forced answers by method name, e.g. `sessions.start`; a method with none stores/answers `ok`. */
  forced = new Map<string, unknown>();
  /** Methods whose promise rejects (the bridge itself failing). */
  rejecting = new Set<string>();
  /** First argument of the last call of each method, e.g. `sessions.stop`. */
  lastArg: Record<string, unknown> = {};
  contactsValue: MedicalContactEntry[] = [];
  dueValue: ReportsDue = { dailyDue: true, weeklyOdiDue: true, lastDailyAt: null, lastWeeklyOdiAt: null };

  /** Records the call, then answers the forced result, rejects, or answers `ok` with `fallback`. */
  private act<T>(name: string, arg: unknown, fallback: T): Promise<WriteResult<T>> {
    this.calls.push(name);
    this.lastArg[name] = arg;
    if (this.rejecting.has(name)) return Promise.reject(new Error('bridge down'));
    const forced = this.forced.get(name);
    return Promise.resolve(forced === undefined ? { ok: true, value: fallback } : (forced as WriteResult<T>));
  }

  sessions = {
    start: (input: { protocolId: string }): Promise<StartSessionResult> => {
      this.calls.push('sessions.start');
      this.lastArg['sessions.start'] = input;
      if (this.rejecting.has('sessions.start')) return Promise.reject(new Error('bridge down'));
      const forced = this.forced.get('sessions.start') as StartSessionResult | undefined;
      return Promise.resolve(forced ?? { ok: true, value: { sessionId: 'started-session', state: 'starting' } });
    },
    stop: (input: StopInput): Promise<WriteResult<CommandSent>> => this.act('sessions.stop', input, { commandId: 'cmd-stop' }),
    markArtifact: (input: { sessionId: string; repIndex: number | null }): Promise<WriteResult<CommandSent>> =>
      this.act('sessions.markArtifact', input, { commandId: 'cmd-artifact' }),
    calibrateMvc: (input: { sessionId: string }): Promise<WriteResult<CommandSent>> =>
      this.act('sessions.calibrateMvc', input, { commandId: 'cmd-mvc' }),
    reprocess: (input: { sessionId: string }): Promise<WriteResult<{ sessionId: string }>> =>
      this.act('sessions.reprocess', input, { sessionId: input.sessionId }),
  };

  reports = {
    saveDaily: (fields: DailyReportFields): Promise<WriteResult<ReportSaved>> =>
      this.act('reports.saveDaily', fields, { reportId: 'report-1', assessmentLevel: 'green', assessmentSkipped: null, exercise: null }),
    saveOswestry: (input: { items: OdiAnswer[] }): Promise<WriteResult<ReportSaved>> =>
      this.act('reports.saveOswestry', input, { reportId: 'report-2', assessmentLevel: null, assessmentSkipped: 'no_baseline', exercise: null }),
    addExercise: (entry: ExerciseEntry): Promise<AddExerciseResult> => {
      this.calls.push('reports.addExercise');
      this.lastArg['reports.addExercise'] = entry;
      if (this.rejecting.has('reports.addExercise')) return Promise.reject(new Error('bridge down'));
      const forced = this.forced.get('reports.addExercise') as AddExerciseResult | undefined;
      return Promise.resolve(forced ?? { ok: true, value: DEFAULT_CONTEXT });
    },
    due: (): Promise<ReportsDue> => {
      this.calls.push('reports.due');
      return Promise.resolve(this.dueValue);
    },
  };

  urgent = {
    send: (input: UrgentInput): Promise<WriteResult<ReportSaved>> =>
      this.act('urgent.send', input, { reportId: 'urgent-1', assessmentLevel: 'red', assessmentSkipped: null, exercise: null }),
  };

  contacts = {
    list: (): Promise<MedicalContactEntry[]> => {
      this.calls.push('contacts.list');
      return Promise.resolve([...this.contactsValue]);
    },
    logOfficerCall: (input: { sessionId: string | null }): Promise<WriteResult<MedicalContactEntry>> =>
      this.act('contacts.logOfficerCall', input, { contactId: 'contact-1', at: '2026-11-05T13:52:00Z', sessionId: input.sessionId }),
  };

  private read<T>(name: keyof FakeAsclepiusApi['readErrors'], value: T): Promise<T> {
    const code = this.readErrors[name];
    return code === undefined ? Promise.resolve(value) : Promise.reject(new Error(code));
  }

  crew = {
    list: (): Promise<CrewMember[]> => {
      this.calls.push('crew.list');
      return Promise.resolve([...this.crewMembers]);
    },
    nextId: (): Promise<string> => {
      this.calls.push('crew.nextId');
      return Promise.resolve(`AST-${String(this.crewMembers.length + 1).padStart(2, '0')}`);
    },
    setHarness: (input: { harnessId: string | null }): Promise<SetHarnessResult> => {
      this.calls.push('crew.setHarness');
      this.lastHarnessId = input.harnessId;
      if (this.harnessFailure !== undefined) return Promise.resolve({ ok: false, reason: this.harnessFailure });
      const member = this.crewMembers.find((m) => m.id === this.session?.crewId);
      if (member === undefined) return Promise.resolve({ ok: false, reason: 'locked' });
      member.harnessId = input.harnessId;
      return Promise.resolve({ ok: true, member: { ...member } });
    },
    create: (input: { name: string; pin: string }): Promise<CreateCrewResult> => {
      this.calls.push('crew.create');
      if (input.name.trim().length < 2) return Promise.resolve({ ok: false, reason: 'name_too_short' });
      if (this.crewMembers.some((m) => m.name.toLowerCase() === input.name.trim().toLowerCase())) {
        return Promise.resolve({ ok: false, reason: 'name_taken' });
      }
      const member: CrewMember = {
        id: `AST-${String(this.crewMembers.length + 1).padStart(2, '0')}`,
        name: input.name.trim(),
        harnessId: null,
      };
      this.crewMembers.push(member);
      this.pins.set(member.id, input.pin);
      this.session = { crewId: member.id, locked: false };
      return Promise.resolve({ ok: true, member });
    },
  };

  auth = {
    verifyPin: (input: { crewId: string; pin: string }): Promise<PinResult> => {
      this.calls.push('auth.verifyPin');
      const expected = this.pins.get(input.crewId);
      if (expected === undefined) return Promise.resolve({ ok: false, reason: 'unknown_crew' });
      const wrong = this.wrong.get(input.crewId) ?? 0;
      if (wrong >= MAX_TRIES) return Promise.resolve({ ok: false, reason: 'locked', retryAfterMs: LOCK_MS });
      if (input.pin === expected) {
        this.wrong.set(input.crewId, 0);
        this.session = { crewId: input.crewId, locked: false };
        return Promise.resolve({ ok: true, crewId: input.crewId });
      }
      this.wrong.set(input.crewId, wrong + 1);
      if (wrong + 1 >= MAX_TRIES) return Promise.resolve({ ok: false, reason: 'locked', retryAfterMs: LOCK_MS });
      return Promise.resolve({ ok: false, reason: 'wrong_pin', attemptsLeft: MAX_TRIES - wrong - 1 });
    },
    lock: (): Promise<void> => {
      this.calls.push('auth.lock');
      if (this.session) this.session = { ...this.session, locked: true };
      return Promise.resolve();
    },
    signOut: (): Promise<void> => {
      this.calls.push('auth.signOut');
      this.session = null;
      return Promise.resolve();
    },
    current: (): Promise<AuthSession | null> => {
      this.calls.push('auth.current');
      return Promise.resolve(this.session);
    },
  };

  /** Canned answers of `views.*`; a view with none rejects `unavailable`, like a backend that is down. `sessions` is by session id. */
  viewData: {
    strip?: StripView;
    today?: TodayView;
    history?: HistoryView;
    baseline?: BaselineView;
    firstDay?: FirstDayResult;
    sessions?: Record<string, SessionView>;
    houston?: HoustonView;
    outbox?: OutboxView;
  } = {};
  /** Makes a view reject with this code instead of answering. */
  viewErrors: Partial<Record<'strip' | 'today' | 'history' | 'baseline' | 'firstDay' | 'session' | 'houston' | 'outbox', ViewsErrorCode>> = {};
  /** Range of the last `views.history` call. */
  historyRange: ViewRange | undefined;

  views = {
    strip: () => this.view('strip', this.viewData.strip),
    today: () => this.view('today', this.viewData.today),
    history: (range?: ViewRange) => {
      this.historyRange = range;
      return this.view('history', this.viewData.history);
    },
    baseline: () => this.view('baseline', this.viewData.baseline),
    firstDay: () => this.view('firstDay', this.viewData.firstDay),
    session: (sessionId: string) => this.view('session', this.viewData.sessions?.[sessionId]),
    houston: () => this.view('houston', this.viewData.houston),
    outbox: () => this.view('outbox', this.viewData.outbox),
  };

  /** Questions asked through `chat.ask`, in order; tests answer them with `emitChat`. */
  chatAsks: { conversationId: string; question: string; emit: (event: ChatEvent) => void; cancelled: boolean }[] = [];
  /** Conversations forgotten through `chat.forget`, in order. */
  chatForgotten: string[] = [];

  chat = {
    ask: (input: { conversationId: string; question: string }, onEvent: (event: ChatEvent) => void): (() => void) => {
      this.calls.push('chat.ask');
      const ask = { ...input, emit: onEvent, cancelled: false };
      this.chatAsks.push(ask);
      return () => {
        if (ask.cancelled) return;
        ask.cancelled = true;
        this.calls.push('chat.cancel');
      };
    },
    forget: (input: { conversationId: string }): Promise<void> => {
      this.calls.push('chat.forget');
      this.chatForgotten.push(input.conversationId);
      return Promise.resolve();
    },
  };

  /** Pushes an event to the last question asked (or the `index`-th), as main would; nothing reaches a cancelled one. */
  emitChat(event: ChatEvent, index = this.chatAsks.length - 1): void {
    const ask = this.chatAsks[index];
    if (ask !== undefined && !ask.cancelled) ask.emit(event);
  }

  private repsListeners = new Map<string, Set<(event: RepsEvent) => void>>();

  /** Live counter: registers a listener per session; tests push events with `emitReps`. */
  reps = {
    subscribe: (sessionId: string, onEvent: (event: RepsEvent) => void): (() => void) => {
      this.calls.push('reps.subscribe');
      const listeners = this.repsListeners.get(sessionId) ?? new Set();
      listeners.add(onEvent);
      this.repsListeners.set(sessionId, listeners);
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        this.calls.push('reps.unsubscribe');
        listeners.delete(onEvent);
      };
    },
  };

  /** Pushes an event to every listener subscribed to `sessionId`, as main would. */
  emitReps(sessionId: string, event: RepsEvent): void {
    for (const listener of [...(this.repsListeners.get(sessionId) ?? [])]) listener(event);
  }

  repsListenerCount(sessionId: string): number {
    return this.repsListeners.get(sessionId)?.size ?? 0;
  }

  private view<T>(name: keyof FakeAsclepiusApi['viewErrors'], data: T | undefined): Promise<T> {
    this.calls.push(`views.${name}`);
    const code = this.viewErrors[name] ?? (data === undefined ? 'unavailable' : undefined);
    if (code !== undefined) return Promise.reject(new Error(code));
    return Promise.resolve(data as T);
  }

  settings = {
    get: (): Promise<Settings> => {
      this.calls.push('settings.get');
      return Promise.resolve({ ...this.settingsValue });
    },
    update: (patch: Partial<Settings>): Promise<Settings> => {
      this.calls.push('settings.update');
      this.settingsValue = { ...this.settingsValue, ...patch };
      return Promise.resolve({ ...this.settingsValue });
    },
  };
}
