/**
 * The unlocked astronaut's profile. The backend replaces the whole profile on each PUT, so an edit first reads the
 * stored one and keeps every field the UI does not edit; when it cannot be read nothing is written (never a blind
 * overwrite). `schema_version` and `updated_at` are left to the backend. No name ever goes in.
 *
 * After a stored edit, today's daily context is derived from the (maybe new) launch date if there is none yet.
 */
import type { AstronautProfile, ProfileFields, WriteResult } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import type { RecordsClient } from '../views/records-client';
import type { WriteClient } from '../views/write-client';
import type { DailyContextService } from './daily-context-service';
import { readFailure, requireUnlocked, unlockedId } from './unlocked';

export interface ProfileService {
  get(): Promise<AstronautProfile | null>;
  update(fields: ProfileFields): Promise<WriteResult<AstronautProfile>>;
}

export interface ProfileServiceOptions {
  auth: Pick<AuthService, 'current'>;
  records: Pick<RecordsClient, 'profile'>;
  writer: Pick<WriteClient, 'putProfile'>;
  dailyContext: Pick<DailyContextService, 'ensureToday'>;
  logError?: (error: unknown) => void;
}

/** The full body to PUT: `stored` with the edited fields over it. */
function profileBody(astronautId: string, stored: AstronautProfile | null, fields: ProfileFields): Record<string, unknown> {
  const { schema_version: _version, updated_at: _updatedAt, ...kept } = stored ?? {};
  const { height_standing_cm, height_sitting_cm, body_mass_kg, ...top } = fields;
  const { height_sitting_cm: _sitting, ...anthropometry } = stored?.anthropometry ?? {};
  return {
    ...kept,
    astronaut_id: astronautId,
    ...top,
    anthropometry: {
      ...anthropometry,
      height_standing_cm,
      body_mass_kg,
      ...(height_sitting_cm === null ? {} : { height_sitting_cm }),
    },
  };
}

export function createProfileService({
  auth,
  records,
  writer,
  dailyContext,
  logError = () => undefined,
}: ProfileServiceOptions): ProfileService {
  return {
    get: async () => records.profile(await requireUnlocked(auth)),

    async update(fields) {
      const id = unlockedId(auth);
      if (id === null) return { ok: false, reason: 'locked' };
      let stored: AstronautProfile | null;
      try {
        stored = await records.profile(id);
      } catch (error) {
        return readFailure(error, logError);
      }
      const result = await writer.putProfile(id, profileBody(id, stored, fields));
      if (result.ok) void dailyContext.ensureToday(id);
      return result;
    },
  };
}
