/** Contract between the preload bridge (main process) and the renderer. Types only. */

/** `id` is the astronaut identifier, `AST-NN`. */
export interface CrewMember {
  id: string;
  name: string;
  harnessId: string;
  hasPin: boolean;
}

export type PinResult =
  | { ok: true; crewId: string }
  | { ok: false; reason: 'wrong_pin' | 'locked' | 'no_pin' | 'unavailable'; attemptsLeft: number };

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
    create(input: { name: string }): Promise<CrewMember>;
    setPin(input: { crewId: string; pin: string }): Promise<void>;
  };
  auth: {
    verifyPin(input: { crewId: string; pin: string }): Promise<PinResult>;
    lock(): Promise<void>;
    current(): Promise<{ crewId: string } | null>;
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
