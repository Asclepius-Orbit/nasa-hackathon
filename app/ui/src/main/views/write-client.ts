/**
 * HTTP client for the backend's UI writes (contract: spine_monitor/contracts/ui-openapi.yaml). Same rules as the
 * views client: loopback origin only, the /ui token and the unlocked astronaut in the headers, redirects refused,
 * one timeout for the whole exchange, and the Node global `fetch` (no system proxy).
 *
 * The answer is a `WriteResult`, branched on the HTTP status only (the problem `type` is not reliable): a 2xx whose
 * body passes the minimum shape check is `ok`; 401/403 `locked`; 422 `invalid` with the field pointers; 409
 * `conflict`; anything else (other status, network error, timeout, redirect, bad body) `unavailable`, with the cause
 * logged here. Of the backend's text only the 422 pointers and messages are forwarded.
 */
import { z } from 'zod';

import type { AstronautProfile, DailyContext, FieldError, WriteResult } from '../../shared/api';
import { type AuthHeaders, authHeaders } from './backend-auth';
import { parseBackendUrl } from './backend-url';
import { DEFAULT_TIMEOUT_MS } from './views-client';

export type WriteMethod = 'PUT' | 'POST';

/** The part of `fetch` used here, so tests can pass a hand-written fake. */
export type WriteFetch = (
  url: string,
  init: {
    method: WriteMethod;
    /** `content-type` and `body` only when the request has a body. */
    headers: { accept: string; 'content-type'?: string } & AuthHeaders;
    body?: string;
    redirect: 'error';
    signal: AbortSignal;
  },
) => Promise<Response>;

export interface WriteRequest<T> {
  method: WriteMethod;
  /** Path under the backend origin, already encoded. */
  path: string;
  /** The unlocked astronaut the request is made for. */
  astronautId: string;
  /** `undefined` sends no body (e.g. `POST reprocess`). */
  body: unknown;
  /** Minimum shape of a 2xx answer; the backend validates the full schema. */
  schema: z.ZodType<T>;
}

export interface WriteClient {
  send<T>(request: WriteRequest<T>): Promise<WriteResult<T>>;
  putProfile(astronautId: string, body: Record<string, unknown>): Promise<WriteResult<AstronautProfile>>;
  putDailyContext(astronautId: string, day: string, body: Record<string, unknown>): Promise<WriteResult<DailyContext>>;
}

export interface WriteClientOptions {
  /** Origin of the backend; must be loopback (see `parseBackendUrl`). */
  baseUrl: string;
  fetch?: WriteFetch;
  timeoutMs?: number;
  /** Shared token of the /ui API (`backend-auth`); null sends none. */
  token?: string | null;
  logError?: (error: unknown) => void;
}

const CONTRACT_MAJOR = z.string().regex(/^1\.\d+\.\d+$/u);

export const profileSchema = (astronautId: string) =>
  z.looseObject({ schema_version: CONTRACT_MAJOR, astronaut_id: z.literal(astronautId), anthropometry: z.looseObject({}) });

export const dailyContextSchema = (astronautId: string, day: string) =>
  z.looseObject({
    schema_version: CONTRACT_MAJOR,
    astronaut_id: z.literal(astronautId),
    date: z.literal(day),
    phase: z.string(),
    gravity_environment: z.string(),
  });

const fieldErrorSchema = z.looseObject({ pointer: z.string(), message: z.string() });

/** Only well-formed `{pointer, message}` entries of `errors`, stripped of anything else. */
function fieldsOf(body: unknown): FieldError[] {
  const errors = z.looseObject({ errors: z.array(z.unknown()) }).safeParse(body);
  if (!errors.success) return [];
  return errors.data.errors.flatMap((entry) => {
    const parsed = fieldErrorSchema.safeParse(entry);
    return parsed.success ? [{ pointer: parsed.data.pointer, message: parsed.data.message }] : [];
  });
}

export function createWriteClient({
  baseUrl,
  fetch: doFetch = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  token = null,
  logError = () => undefined,
}: WriteClientOptions): WriteClient {
  const origin = parseBackendUrl(baseUrl);
  if (origin === null) throw new Error('backend URL must be an http(s) loopback origin');
  const base: string = origin;

  async function send<T>({ method, path, astronautId, body, schema }: WriteRequest<T>): Promise<WriteResult<T>> {
    try {
      const content = body === undefined ? {} : { body: JSON.stringify(body) };
      const response = await doFetch(`${base}${path}`, {
        method,
        headers: {
          accept: 'application/json',
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
          ...authHeaders(token, astronautId),
        },
        ...content,
        redirect: 'error',
        signal: AbortSignal.timeout(timeoutMs),
      });
      const { status } = response;
      if (status === 401 || status === 403) return { ok: false, reason: 'locked' };
      if (status === 409) return { ok: false, reason: 'conflict' };
      if (status === 422) return { ok: false, reason: 'invalid', fields: fieldsOf(await response.json().catch(() => null)) };
      if (status < 200 || status > 299) throw new Error(`backend answered ${String(status)} to ${method} ${path}`);
      const parsed = schema.safeParse(await response.json());
      if (!parsed.success) throw new Error(`${method} ${path}: answer outside the contract`, { cause: parsed.error });
      // The minimum shape holds; the full contract is the backend's responsibility.
      return { ok: true, value: parsed.data };
    } catch (error) {
      logError(error);
      return { ok: false, reason: 'unavailable' };
    }
  }

  return {
    send,
    putProfile: (id, body) =>
      send({
        method: 'PUT',
        path: `/ui/crew/${encodeURIComponent(id)}/profile`,
        astronautId: id,
        body,
        schema: profileSchema(id) as unknown as z.ZodType<AstronautProfile>,
      }),
    putDailyContext: (id, day, body) =>
      send({
        method: 'PUT',
        path: `/ui/crew/${encodeURIComponent(id)}/daily-context/${encodeURIComponent(day)}`,
        astronautId: id,
        body,
        schema: dailyContextSchema(id, day) as unknown as z.ZodType<DailyContext>,
      }),
  };
}
