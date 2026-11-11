/**
 * Reads of the records the UI also writes (profile, daily context, self-reports, medical contacts), of the devices to
 * pair and of a session's declared streams, from the backend's /ui API. Same transport rules as the views client. A
 * record that is not stored (404) is `null` (no devices or contacts: `[]`; reports due always exist, so `unavailable`);
 * 401/403 is `locked`; anything else (other status, network error, timeout, body outside the contract) `unavailable`.
 */
import { z } from 'zod';

import type { AstronautProfile, DailyContext, DeviceStatus, MedicalContactEntry, ReportsDue } from '../../shared/api';
import { authHeaders } from './backend-auth';
import { parseBackendUrl } from './backend-url';
import { DEFAULT_TIMEOUT_MS, type ViewFetch } from './views-client';
import { ViewsError } from './views-error';
import { dailyContextSchema, profileSchema } from './write-client';

export interface RecordsClient {
  profile(astronautId: string): Promise<AstronautProfile | null>;
  dailyContext(astronautId: string, day: string): Promise<DailyContext | null>;
  devices(astronautId: string): Promise<DeviceStatus[]>;
  /**
   * Streams declared in a session (`GET /ui/sessions/{id}`, the raw session: the session view carries no stream kinds);
   * `null` for an unknown session.
   */
  sessionStreams(astronautId: string, sessionId: string): Promise<SessionStream[] | null>;
  reportsDue(astronautId: string): Promise<ReportsDue>;
  contacts(astronautId: string): Promise<MedicalContactEntry[]>;
}

/** A stream declared in a session (`domain/session` `streams[]`), reduced to what the UI needs. */
export interface SessionStream {
  streamId: string;
  kind: string;
}

export interface RecordsClientOptions {
  baseUrl: string;
  fetch?: ViewFetch;
  timeoutMs?: number;
  token?: string | null;
}

const devicesSchema = (astronautId: string) =>
  z.array(z.looseObject({ astronaut_id: z.literal(astronautId), device_id: z.string(), status: z.string(), source: z.string() }));

const rawSessionSchema = (astronautId: string) =>
  z.looseObject({
    session: z.looseObject({
      astronaut_id: z.literal(astronautId),
      streams: z.array(z.looseObject({ stream_id: z.string(), stream_kind: z.string() })),
    }),
  });
type RawSession = z.infer<ReturnType<typeof rawSessionSchema>>;

const dueSchema = z.looseObject({
  daily_due: z.boolean(),
  weekly_odi_due: z.boolean(),
  last_daily_at: z.string().nullable(),
  last_weekly_odi_at: z.string().nullable(),
});
type Due = z.infer<typeof dueSchema>;

const contactListSchema = (astronautId: string) =>
  z.looseObject({
    astronaut_id: z.literal(astronautId),
    contacts: z.array(z.looseObject({ contact_id: z.string(), at: z.string(), session_id: z.string().optional() })),
  });
type ContactList = z.infer<ReturnType<typeof contactListSchema>>;

export function createRecordsClient({
  baseUrl,
  fetch: doFetch = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  token = null,
}: RecordsClientOptions): RecordsClient {
  const origin = parseBackendUrl(baseUrl);
  if (origin === null) throw new Error('backend URL must be an http(s) loopback origin');
  const base: string = origin;

  async function get<T>(path: string, schema: z.ZodType, astronautId: string): Promise<T | null> {
    let body: unknown;
    try {
      const response = await doFetch(`${base}${path}`, {
        method: 'GET',
        headers: { accept: 'application/json', ...authHeaders(token, astronautId) },
        redirect: 'error',
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (response.status === 404) return null;
      if (response.status === 401 || response.status === 403) throw new ViewsError('locked');
      if (response.status !== 200) throw new ViewsError('unavailable');
      body = await response.json();
    } catch (error) {
      if (error instanceof ViewsError) throw error;
      throw new ViewsError('unavailable', { cause: error });
    }
    const parsed = schema.safeParse(body);
    if (!parsed.success) throw new ViewsError('unavailable', { cause: parsed.error });
    return body as T;
  }

  const crewPath = (id: string, rest: string): string => `/ui/crew/${encodeURIComponent(id)}/${rest}`;

  return {
    profile: (id) => get(crewPath(id, 'profile'), profileSchema(id), id),
    dailyContext: (id, day) => get(crewPath(id, `daily-context/${encodeURIComponent(day)}`), dailyContextSchema(id, day), id),
    devices: async (id) => (await get<DeviceStatus[]>(crewPath(id, 'devices'), devicesSchema(id), id)) ?? [],
    async sessionStreams(id, sessionId) {
      const body = await get<RawSession>(`/ui/sessions/${encodeURIComponent(sessionId)}`, rawSessionSchema(id), id);
      return body === null ? null : body.session.streams.map((s) => ({ streamId: s.stream_id, kind: s.stream_kind }));
    },
    async reportsDue(id) {
      const due = await get<Due>(crewPath(id, 'self-reports/due'), dueSchema, id);
      if (due === null) throw new ViewsError('unavailable');
      return {
        dailyDue: due.daily_due,
        weeklyOdiDue: due.weekly_odi_due,
        lastDailyAt: due.last_daily_at,
        lastWeeklyOdiAt: due.last_weekly_odi_at,
      };
    },
    async contacts(id) {
      const list = await get<ContactList>(crewPath(id, 'contacts'), contactListSchema(id), id);
      return (list?.contacts ?? []).map((c) => ({ contactId: c.contact_id, at: c.at, sessionId: c.session_id ?? null }));
    },
  };
}
