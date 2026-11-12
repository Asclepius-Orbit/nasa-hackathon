/**
 * Reads of the records the UI also writes (profile, daily context) and of the devices to pair, from the backend's
 * /ui API. Same transport rules as the views client. A record that is not stored (404) is `null` (no devices: `[]`);
 * 401/403 is `locked`; anything else (other status, network error, timeout, body outside the contract) `unavailable`.
 */
import { z } from 'zod';

import type { AstronautProfile, DailyContext, DeviceStatus } from '../../shared/api';
import { authHeaders } from './backend-auth';
import { parseBackendUrl } from './backend-url';
import { DEFAULT_TIMEOUT_MS, type ViewFetch } from './views-client';
import { ViewsError } from './views-error';
import { dailyContextSchema, profileSchema } from './write-client';

export interface RecordsClient {
  profile(astronautId: string): Promise<AstronautProfile | null>;
  profileVersioned(astronautId: string): Promise<{ value: AstronautProfile; etag: string } | null>;
  dailyContext(astronautId: string, day: string): Promise<DailyContext | null>;
  dailyContextVersioned(astronautId: string, day: string): Promise<{ value: DailyContext; etag: string } | null>;
  devices(astronautId: string): Promise<DeviceStatus[]>;
}

export interface RecordsClientOptions {
  baseUrl: string;
  fetch?: ViewFetch;
  timeoutMs?: number;
  token?: string | null;
}

const devicesSchema = (astronautId: string) =>
  z.array(z.looseObject({ astronaut_id: z.literal(astronautId), device_id: z.string(), status: z.string(), source: z.string() }));

export function createRecordsClient({
  baseUrl,
  fetch: doFetch = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  token = null,
}: RecordsClientOptions): RecordsClient {
  const origin = parseBackendUrl(baseUrl);
  if (origin === null) throw new Error('backend URL must be an http(s) loopback origin');
  const base: string = origin;

  async function get<T>(path: string, schema: z.ZodType, astronautId: string, onEtag?: (etag: string | null) => void): Promise<T | null> {
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
      if (onEtag !== undefined) onEtag(response.headers.get('etag'));
      body = await response.json();
    } catch (error) {
      if (error instanceof ViewsError) throw error;
      throw new ViewsError('unavailable', { cause: error });
    }
    const parsed = schema.safeParse(body);
    if (!parsed.success) throw new ViewsError('unavailable', { cause: parsed.error });
    return body as T;
  }

  async function versioned<T>(path: string, schema: z.ZodType<T>, id: string): Promise<{ value: T; etag: string } | null> {
    const metadata: { etag: string | null } = { etag: null };
    const value = await get<T>(path, schema, id, (header) => { metadata.etag = header; });
    if (value === null) return null;
    if (metadata.etag === null) throw new ViewsError('unavailable');
    return { value, etag: metadata.etag };
  }

  const crewPath = (id: string, rest: string): string => `/ui/crew/${encodeURIComponent(id)}/${rest}`;

  return {
    profile: (id) => get(crewPath(id, 'profile'), profileSchema(id), id),
    profileVersioned: (id) => versioned(crewPath(id, 'profile'), profileSchema(id) as unknown as z.ZodType<AstronautProfile>, id),
    dailyContext: (id, day) => get(crewPath(id, `daily-context/${encodeURIComponent(day)}`), dailyContextSchema(id, day), id),
    dailyContextVersioned: (id, day) => versioned(crewPath(id, `daily-context/${encodeURIComponent(day)}`), dailyContextSchema(id, day) as unknown as z.ZodType<DailyContext>, id),
    devices: async (id) => (await get<DeviceStatus[]>(crewPath(id, 'devices'), devicesSchema(id), id)) ?? [],
  };
}
