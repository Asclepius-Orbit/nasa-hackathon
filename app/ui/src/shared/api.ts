/** Contract between the preload bridge (main process) and the renderer. Types only. */

/** `id` is the astronaut identifier, `AST-NN`. Names stay on this device; the bus and Houston only see `id`. */
export interface CrewMember {
  id: string;
  name: string;
  /** `null` until a harness is paired ("not paired yet" in the UI). */
  harnessId: string | null;
}

/** Wrong PIN 3 times locks that crew member for 5 minutes (mock: "Wait 5 min or ask the crew medical officer"). */
export type PinResult =
  | { ok: true; crewId: string }
  | { ok: false; reason: 'wrong_pin'; attemptsLeft: number }
  | { ok: false; reason: 'locked'; retryAfterMs: number }
  | { ok: false; reason: 'unknown_crew' | 'unavailable' };

/** A new member is stored only once the PIN has been chosen twice and matches; success also signs them in. */
export type CreateCrewResult =
  | { ok: true; member: CrewMember }
  | { ok: false; reason: 'name_too_short' | 'name_taken' | 'invalid_pin' | 'unavailable' };

/** The signed-in crew member; `locked` means the lock screen is up and only their PIN unlocks it. */
export interface AuthSession {
  crewId: string;
  locked: boolean;
}

export interface Settings {
  lengthUnit: 'mm' | 'in';
  timeFormat: 'gmt' | 'gmt_met';
  textSize: 'standard' | 'large';
  voiceCommands: boolean;
  footPedal: boolean;
}

export interface AsclepiusApi {
  crew: {
    list(): Promise<CrewMember[]>;
    /** Crew code the next new member would get, e.g. `AST-03` (shown before they finish). */
    nextId(): Promise<string>;
    create(input: { name: string; pin: string }): Promise<CreateCrewResult>;
  };
  auth: {
    /** Signs in from the login screen, or unlocks the lock screen for the session's crew member. */
    verifyPin(input: { crewId: string; pin: string }): Promise<PinResult>;
    lock(): Promise<void>;
    current(): Promise<AuthSession | null>;
  };
  settings: {
    get(): Promise<Settings>;
    update(patch: Partial<Settings>): Promise<Settings>;
  };
}

declare global {
  interface Window {
    asclepius: AsclepiusApi;
  }
}
