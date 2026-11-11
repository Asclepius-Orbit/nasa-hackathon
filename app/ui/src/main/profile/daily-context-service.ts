/**
 * Today's daily context (UTC calendar day) of the unlocked astronaut. An edit keeps everything the UI does not edit
 * (medication, exercise, countermeasures...) as stored; when the current one cannot be read nothing is written.
 *
 * `ensureToday` fills in a missing day from the profile's launch and landing dates, with the backend's rules
 * (`spine_monitor/backend/src/monitor/views/mission.py`). It never overwrites a stored context and never fails: it is
 * best effort, run after sign-in and after a profile update.
 */
import type { AstronautProfile, DailyContext, DailyContextFields, WriteResult } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import type { RecordsClient } from '../views/records-client';
import type { WriteClient } from '../views/write-client';
import { readFailure, requireUnlocked, unlockedId } from './unlocked';

export interface DailyContextService {
  today(): Promise<DailyContext | null>;
  updateToday(fields: DailyContextFields): Promise<WriteResult<DailyContext>>;
  /** Writes today's context derived from the profile when none is stored. Resolves once done; never rejects. */
  ensureToday(astronautId: string): Promise<void>;
}

export interface DailyContextServiceOptions {
  auth: Pick<AuthService, 'current'>;
  records: Pick<RecordsClient, 'profile' | 'dailyContext'>;
  writer: Pick<WriteClient, 'putDailyContext'>;
  now: () => number;
  logError?: (error: unknown) => void;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** `YYYY-MM-DD` of the UTC day containing `ms`. */
export function utcDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

const dayNumber = (isoDay: string): number => Date.parse(`${isoDay}T00:00:00Z`) / DAY_MS;

/**
 * Phase, gravity and mission day of `day` from the profile: launch day is 1, the day before -1 (no day 0); preflight
 * before launch, postflight from the landing day on, inflight (microgravity) in between. `null` without a launch date.
 */
export function deriveDailyContext(
  profile: Pick<AstronautProfile, 'launch_date' | 'landing_date'>,
  day: string,
): DailyContextFields | null {
  const launch = profile.launch_date;
  if (launch === undefined || launch === null) return null;
  const elapsed = dayNumber(day) - dayNumber(launch);
  const landing = profile.landing_date;
  const phase =
    elapsed < 0 ? 'preflight' : landing !== undefined && landing !== null && day >= landing ? 'postflight' : 'inflight';
  return {
    phase,
    gravity_environment: phase === 'inflight' ? 'microgravity' : 'earth_1g',
    mission_day: elapsed < 0 ? elapsed : elapsed + 1,
  };
}

function contextBody(astronautId: string, day: string, kept: Record<string, unknown>, fields: DailyContextFields) {
  const { mission_day: missionDay, ...rest } = fields;
  return {
    ...kept,
    astronaut_id: astronautId,
    date: day,
    ...rest,
    ...(missionDay === null ? {} : { mission_day: missionDay }),
  };
}

export function createDailyContextService({
  auth,
  records,
  writer,
  now,
  logError = () => undefined,
}: DailyContextServiceOptions): DailyContextService {
  return {
    today: async () => records.dailyContext(await requireUnlocked(auth), utcDay(now())),

    async updateToday(fields) {
      const id = unlockedId(auth);
      if (id === null) return { ok: false, reason: 'locked' };
      const day = utcDay(now());
      let stored: DailyContext | null;
      try {
        stored = await records.dailyContext(id, day);
      } catch (error) {
        return readFailure(error, logError);
      }
      // The backend sets the contract version; a cleared mission day must not survive from the stored one.
      const { schema_version: _version, mission_day: _missionDay, ...kept } = stored ?? {};
      return writer.putDailyContext(id, day, contextBody(id, day, kept, fields));
    },

    async ensureToday(astronautId) {
      const day = utcDay(now());
      try {
        if ((await records.dailyContext(astronautId, day)) !== null) return;
        const profile = await records.profile(astronautId);
        const derived = profile === null ? null : deriveDailyContext(profile, day);
        if (derived === null) return;
        const result = await writer.putDailyContext(astronautId, day, contextBody(astronautId, day, {}, derived));
        if (!result.ok) logError(`daily context: ${day} not stored automatically (${result.reason})`);
      } catch (error) {
        logError(error);
      }
    },
  };
}
