import type {
  AsclepiusApi,
  AuthSession,
  BaselineView,
  CreateCrewResult,
  CrewMember,
  FirstDayResult,
  HistoryView,
  HoustonView,
  OutboxView,
  PinResult,
  RepsEvent,
  SessionView,
  Settings,
  StripView,
  TodayView,
  ViewRange,
  ViewsErrorCode,
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

  crew = {
    list: (): Promise<CrewMember[]> => {
      this.calls.push('crew.list');
      return Promise.resolve([...this.crewMembers]);
    },
    nextId: (): Promise<string> => {
      this.calls.push('crew.nextId');
      return Promise.resolve(`AST-${String(this.crewMembers.length + 1).padStart(2, '0')}`);
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
