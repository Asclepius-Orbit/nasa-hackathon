/**
 * HTTP client for the backend's UI views (contract: spine_monitor/contracts/ui-openapi.yaml). Runs in the main
 * process only, so the renderer's CSP (`connect-src 'self'`) stays untouched.
 *
 * The client never invents or caches data: a 404 is `not_found`; a network failure, timeout, any other status,
 * or a body that is not JSON of the expected shape is `unavailable`. Redirects are refused (a loopback server must
 * not be able to send the request elsewhere). The Node global `fetch` is used on purpose: unlike Electron's
 * `net.fetch` it ignores system proxy settings, which must never sit between the app and its local backend.
 *
 * Response bodies are checked for their minimum shape only (contract major, the keys the UI reads, and identity of
 * the astronaut or session asked for); the backend validates its output against the full schemas.
 */
import { z } from 'zod';

import type {
  BaselineView,
  FirstDayView,
  HistoryView,
  HoustonView,
  OutboxView,
  SessionView,
  StripView,
  TodayView,
} from '../../contracts/v1';
import type { ViewRange } from '../../shared/api';
import { type AuthHeaders, authHeaders } from './backend-auth';
import { parseBackendUrl } from './backend-url';
import { ViewsError } from './views-error';

export const DEFAULT_TIMEOUT_MS = 5_000;

/** The part of `fetch` used here, so tests can pass a hand-written fake. */
export type ViewFetch = (
  url: string,
  init: { method: 'GET'; headers: { accept: string } & AuthHeaders; redirect: 'error'; signal: AbortSignal },
) => Promise<Response>;

export interface ViewsClient {
  strip(astronautId: string): Promise<StripView>;
  today(astronautId: string): Promise<TodayView>;
  history(astronautId: string, range: ViewRange): Promise<HistoryView>;
  baseline(astronautId: string): Promise<BaselineView>;
  firstDay(astronautId: string): Promise<FirstDayView>;
  /** The session view, but only if it belongs to `astronautId`: someone else's session is `not_found`. */
  session(astronautId: string, sessionId: string): Promise<SessionView>;
  /** What goes to the ground station and the link state; `summary` is `null` while there is none (still a 200). */
  houston(astronautId: string): Promise<HoustonView>;
  /** Summaries queued for or sent to the ground station. */
  outbox(astronautId: string): Promise<OutboxView>;
}

export interface ViewsClientOptions {
  /** Origin of the backend; must be loopback (see `parseBackendUrl`). */
  baseUrl: string;
  fetch?: ViewFetch;
  timeoutMs?: number;
  /** Shared token of the /ui API (`backend-auth`); null sends none. */
  token?: string | null;
}

const object = z.looseObject({});
const view = <S extends z.ZodRawShape>(shape: S) =>
  z.looseObject({ schema_version: z.string().regex(/^1\.\d+\.\d+$/u), ...shape });

const baselineShape = {
  recorded: z.number(),
  required: z.number(),
  reference_version: z.number().nullable(),
  built_from_sessions: z.number(),
  sessions: z.array(object),
};

const SCHEMAS = {
  strip: (astronautId: string) =>
    view({ astronaut_id: z.literal(astronautId), mission: object, harness: object.nullable() }),
  today: () => view({ level: z.string(), recommendations: z.array(z.string()), reasons: z.array(object) }),
  history: () => view({ from: z.string(), to: z.string(), days: z.array(object) }),
  baseline: () => view(baselineShape),
  firstDay: () => view({ todo: z.array(z.string()), baseline: view(baselineShape) }),
  houston: (astronautId: string) => view({ astronaut_id: z.literal(astronautId), summary: object.nullable(), ground_link: object.nullable() }),
  outbox: (astronautId: string) => view({ astronaut_id: z.literal(astronautId), ground_link: object.nullable(), items: z.array(object) }),
  // `astronaut_id` is compared by the client (a mismatch is not_found, not a malformed body).
  session: (sessionId: string) =>
    view({ session_id: z.literal(sessionId), astronaut_id: z.string(), state: z.string(), protocol_id: z.string() }),
};

export function createViewsClient({
  baseUrl,
  fetch: doFetch = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  token = null,
}: ViewsClientOptions): ViewsClient {
  const origin = parseBackendUrl(baseUrl);
  if (origin === null) throw new Error('backend URL must be an http(s) loopback origin');
  const base: string = origin;

  /** `notFoundStatuses`: statuses shown as not_found (session routes add 403: the guard denies another astronaut's session). */
  /** `astronautId`: the unlocked astronaut the request is made for (the backend checks it owns what is asked). */
  async function get<T>(path: string, schema: z.ZodType, astronautId: string, notFoundStatuses: readonly number[] = [404]): Promise<T> {
    let body: unknown;
    try {
      // One signal for the whole exchange: a server that sends headers and then stalls is cut off as well.
      const response = await doFetch(`${base}${path}`, {
        method: 'GET',
        headers: { accept: 'application/json', ...authHeaders(token, astronautId) },
        redirect: 'error',
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (notFoundStatuses.includes(response.status)) throw new ViewsError('not_found');
      if (response.status !== 200) throw new ViewsError('unavailable');
      body = await response.json();
    } catch (error) {
      if (error instanceof ViewsError) throw error;
      throw new ViewsError('unavailable', { cause: error });
    }
    const parsed = schema.safeParse(body);
    if (!parsed.success) throw new ViewsError('unavailable', { cause: parsed.error });
    // The minimum shape holds; the full contract is the backend's responsibility.
    return body as T;
  }

  const crewPath = (astronautId: string, name: string): string =>
    `/ui/crew/${encodeURIComponent(astronautId)}/views/${name}`;

  return {
    strip: (id) => get(crewPath(id, 'strip'), SCHEMAS.strip(id), id),
    today: (id) => get(crewPath(id, 'today'), SCHEMAS.today(), id),
    history(id, { from, to }) {
      const query = new URLSearchParams();
      if (from !== undefined) query.set('from', from);
      if (to !== undefined) query.set('to', to);
      const suffix = query.size === 0 ? '' : `?${query.toString()}`;
      return get(`${crewPath(id, 'history')}${suffix}`, SCHEMAS.history(), id);
    },
    houston: (id) => get(`/ui/crew/${encodeURIComponent(id)}/houston`, SCHEMAS.houston(id), id),
    outbox: (id) => get(`/ui/crew/${encodeURIComponent(id)}/outbox`, SCHEMAS.outbox(id), id),
    baseline: (id) => get(crewPath(id, 'baseline'), SCHEMAS.baseline(), id),
    firstDay: (id) => get(crewPath(id, 'first-day'), SCHEMAS.firstDay(), id),
    async session(astronautId, sessionId) {
      const body = await get<SessionView>(`/ui/sessions/${encodeURIComponent(sessionId)}/view`, SCHEMAS.session(sessionId), astronautId, [403, 404]);
      // A session of another crew member must look like one that does not exist, never leak across astronauts.
      if (body.astronaut_id !== astronautId) throw new ViewsError('not_found');
      return body;
    },
  };
}
