import type { AsclepiusApi, AuthSession, CreateCrewResult, CrewMember, PinResult, Settings } from '../../shared/api';

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
