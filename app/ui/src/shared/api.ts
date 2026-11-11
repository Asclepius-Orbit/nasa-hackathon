/** Contract between the preload bridge (main process) and the renderer. Types only. */
import type {
  AstronautProfile,
  BaselineView,
  DailyContext,
  DeviceStatus,
  FirstDayView,
  HistoryView,
  HoustonView,
  OutboxView,
  RepStateView,
  SessionView,
  StripView,
  TodayView,
} from '../contracts/v1';

export type {
  AstronautProfile,
  BaselineView,
  DailyContext,
  DeviceStatus,
  FirstDayView,
  HistoryView,
  HoustonView,
  OutboxView,
  RepStateView,
  SessionView,
  StripView,
  TodayView,
};

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

/** Inclusive window of the History view, ISO dates (`YYYY-MM-DD`); each end defaults to the backend's choice. */
export interface ViewRange {
  from?: string;
  to?: string;
}

/** The backend's first-day view plus the signed-in member's first name, which never leaves this device. */
export type FirstDayResult = FirstDayView & { crewFirstName: string };

/**
 * Why a view call rejected, as the message of the thrown `Error`: `locked` (nobody signed in, or the lock screen is
 * up), `not_found` (the backend has no such astronaut or session) or `unavailable` (backend down, slow, or it
 * answered something that is not the contract). Views are never cached or invented.
 */
export type ViewsErrorCode = 'locked' | 'not_found' | 'unavailable';

/** Why a live repetition stream ended in error: a `ViewsErrorCode`, or `no_reps` (the protocol has no repetitions). */
export type RepsErrorCode = ViewsErrorCode | 'no_reps';

/**
 * What a repetition subscription delivers. `state` is the live counter; `end` is its last value (the session
 * ended, no more events follow); `lost` means the connection to the backend dropped and is being retried (the
 * counter on screen is stale until the next `state`); `error` is final.
 */
export type RepsEvent =
  | { kind: 'state'; state: RepStateView }
  | { kind: 'end'; state: RepStateView }
  | { kind: 'lost' }
  | { kind: 'error'; code: RepsErrorCode };

/** What main pushes on `asclepius:reps:event`. The preload hands only `event` to the renderer. */
export interface RepsMessage {
  subscriptionId: string;
  sessionId: string;
  event: RepsEvent;
}

/**
 * Why a write to the backend was not stored: `locked` (nobody unlocked, or the backend refused the credentials),
 * `invalid` (the backend rejected the body; `fields` points into it), `conflict` (it already holds other content under
 * the same id) or `unavailable` (down, slow, or an answer outside the contract). Only `ok: true` means stored.
 */
export type WriteErrorCode = 'locked' | 'invalid' | 'conflict' | 'unavailable';

/** One rejected field: a JSON pointer into the body sent (e.g. `/anthropometry/body_mass_kg`) and the backend's reason. */
export interface FieldError {
  pointer: string;
  message: string;
}

/** Outcome of a write. A result, not a rejection, so the 422 field pointers survive the IPC hop. */
export type WriteResult<T> =
  | { ok: true; value: T }
  | { ok: false; reason: 'invalid'; fields: FieldError[] }
  | { ok: false; reason: Exclude<WriteErrorCode, 'invalid'> };

/**
 * The part of the profile the UI edits (`domain/astronaut-profile`). Main keeps every other stored field as it was,
 * and sets `astronaut_id`; no name ever goes in. `null` clears an optional field.
 */
export interface ProfileFields {
  date_of_birth: string;
  sex_at_birth: AstronautProfile['sex_at_birth'];
  launch_date: string | null;
  landing_date: string | null;
  height_standing_cm: number;
  height_sitting_cm: number | null;
  body_mass_kg: number;
}

/** The part of today's daily context the UI edits (`domain/daily-context`); main keeps the rest and sets the ids and date. */
export interface DailyContextFields {
  phase: DailyContext['phase'];
  gravity_environment: DailyContext['gravity_environment'];
  /** `null` leaves it out (unknown). Launch day is 1, the day before -1: there is no day 0. */
  mission_day: number | null;
}

/** Pairing a harness changes only the unlocked member; `locked` when nobody is, `unavailable` when it could not be stored. */
export type SetHarnessResult = { ok: true; member: CrewMember } | { ok: false; reason: 'locked' | 'unavailable' };

export interface AsclepiusApi {
  crew: {
    list(): Promise<CrewMember[]>;
    /** Crew code the next new member would get, e.g. `AST-03` (shown before they finish). */
    nextId(): Promise<string>;
    create(input: { name: string; pin: string }): Promise<CreateCrewResult>;
    /** Pairs (`harnessId` = a `device_id`) or unpairs (`null`) the unlocked member's harness. Stored only on this device. */
    setHarness(input: { harnessId: string | null }): Promise<SetHarnessResult>;
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
  /** Read-only views from the backend, always for the signed-in astronaut. Rejects with a `ViewsErrorCode`. */
  views: {
    strip(): Promise<StripView>;
    today(): Promise<TodayView>;
    history(range?: ViewRange): Promise<HistoryView>;
    baseline(): Promise<BaselineView>;
    firstDay(): Promise<FirstDayResult>;
    session(sessionId: string): Promise<SessionView>;
    houston(): Promise<HoustonView>;
    outbox(): Promise<OutboxView>;
  };
  /** The unlocked astronaut's profile. `get` is `null` when none is stored and rejects with a `ViewsErrorCode`. */
  profile: {
    get(): Promise<AstronautProfile | null>;
    update(fields: ProfileFields): Promise<WriteResult<AstronautProfile>>;
  };
  /**
   * The unlocked astronaut's daily context for today (UTC). `today` is `null` when none is stored and rejects with a
   * `ViewsErrorCode`. Main also writes one on its own when today has none and the profile has a launch date.
   */
  dailyContext: {
    today(): Promise<DailyContext | null>;
    updateToday(fields: DailyContextFields): Promise<WriteResult<DailyContext>>;
  };
  /** Devices that published a status for the unlocked astronaut (to pair a harness). Rejects with a `ViewsErrorCode`. */
  devices: {
    list(): Promise<DeviceStatus[]>;
  };
  /** Live push from the backend, for the signed-in astronaut's own sessions only. */
  reps: {
    /**
     * Live repetition counter of a session. Returns the function that stops it (safe to call at once, any
     * number of times). A refused subscription arrives as `{ kind: 'error' }` with a `ViewsErrorCode`.
     * Locking the app, or the session ending, stops the events without a further call.
     */
    subscribe(sessionId: string, onEvent: (event: RepsEvent) => void): () => void;
  };
}

declare global {
  interface Window {
    asclepius: AsclepiusApi;
  }
}
