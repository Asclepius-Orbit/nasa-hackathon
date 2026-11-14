/**
 * Live repetition counter: reads the backend's server-sent events for one session
 * (`GET /ui/sessions/{session_id}/reps`, contract: spine_monitor/contracts/ui-openapi.yaml). Runs in the main
 * process only. It does not know who is signed in: whether the session belongs to that astronaut is checked
 * before a stream is opened (see `reps-service.ts`).
 *
 * Like the views client, it refuses non-loopback backends and redirects, uses the Node global `fetch` (no system
 * proxy) and checks each event for its minimum shape only (`repStateSchema`: contract major, field types, and
 * the session asked for). An event that does not pass ends the stream with an error: it is never forwarded.
 *
 * Timing: the connection must answer within `connectTimeoutMs`; after that only silence counts (`idleTimeoutMs`,
 * restarted by any bytes, comments included). There is deliberately no limit on the total time of a stream: a
 * session lasts as long as it lasts. A stream that drops without `end` (network, server restart, silence) is
 * reported as `lost` once and reopened with a capped backoff, for as long as the caller keeps it open; the backend
 * resends the current state when it reconnects. `end`, a 404 (`not_found`), a 409 (`no_reps`) and other 4xx
 * answers are final: nothing is retried, except 408 and 429, which are retried like a server error.
 */
import type { RepsEvent } from '../../shared/api';
import { repStateSchema } from '../../shared/ipc-schema';
import { parseBackendUrl } from './backend-url';
import { createSseParser } from './sse-parser';
import type { ViewFetch } from './views-client';
import { authHeaders } from './backend-auth';

export const DEFAULT_CONNECT_TIMEOUT_MS = 5_000;
/** Silence after which a stream is considered dead. The backend should send a comment line more often than this. */
export const DEFAULT_IDLE_TIMEOUT_MS = 45_000;
/** Wait before the 1st, 2nd, 3rd... reconnection; the last value repeats. */
export const DEFAULT_BACKOFF_MS: readonly number[] = [1_000, 2_000, 5_000];

/** The part of the timers used here, so tests can drive time by hand. */
export interface Scheduler {
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const SYSTEM_SCHEDULER: Scheduler = {
  setTimeout: (callback, ms) => globalThis.setTimeout(callback, ms),
  clearTimeout: (handle) => {
    globalThis.clearTimeout(handle as ReturnType<typeof globalThis.setTimeout>);
  },
};

export interface RepsStreamOptions {
  /** Origin of the backend; must be loopback (see `parseBackendUrl`). */
  baseUrl: string;
  fetch?: ViewFetch;
  scheduler?: Scheduler;
  connectTimeoutMs?: number;
  idleTimeoutMs?: number;
  backoffMs?: readonly number[];
  /** Where the technical cause of a final stream error goes (it never reaches the renderer). */
  logError?: (error: unknown) => void;
  /** Shared token of the /ui API (`backend-auth`); null sends none. */
  token?: string | null;
}

export interface RepsStreamClient {
  /**
   * Starts reading the session's events and returns the function that stops it. `onEvent` gets `state`, `end`,
   * `lost` and `error` events; after `end` or `error` it is never called again. Stopping is idempotent and silent.
   */
  /** `astronautId`: the unlocked astronaut, sent to the backend, which checks the session is theirs. */
  open(sessionId: string, onEvent: (event: RepsEvent) => void, astronautId?: string): () => void;
}

interface Attempt {
  /** `final` when `end` or a terminal error was delivered; `lost` when the connection should be retried. */
  outcome: 'final' | 'lost';
  /** True when at least one valid event arrived, i.e. the connection worked. */
  gotState: boolean;
}

const LOST: Attempt = { outcome: 'lost', gotState: false };
const FINAL: Attempt = { outcome: 'final', gotState: false };

function discard(response: Response): void {
  void response.body?.cancel().catch(() => undefined);
}

export function createRepsStreamClient({
  baseUrl,
  fetch: doFetch = globalThis.fetch,
  scheduler = SYSTEM_SCHEDULER,
  connectTimeoutMs = DEFAULT_CONNECT_TIMEOUT_MS,
  idleTimeoutMs = DEFAULT_IDLE_TIMEOUT_MS,
  backoffMs = DEFAULT_BACKOFF_MS,
  logError = () => undefined,
  token = null,
}: RepsStreamOptions): RepsStreamClient {
  const origin = parseBackendUrl(baseUrl);
  if (origin === null) throw new Error('backend URL must be an http(s) loopback origin');

  return {
    open(sessionId, onEvent, astronautId) {
      const url = `${origin}/ui/sessions/${encodeURIComponent(sessionId)}/reps`;
      let closed = false;
      let controller: AbortController | null = null;
      let retryTimer: unknown;
      let wake: (() => void) | null = null;

      // `closed` changes while `await`s are pending; a function keeps the compiler from assuming it did not.
      const isClosed = (): boolean => closed;

      function emit(event: RepsEvent): void {
        if (closed) return;
        try {
          onEvent(event);
        } catch (error) {
          logError(error);
        }
      }

      /** One connection, from the request to the end of its body. Never throws. */
      async function connect(): Promise<Attempt> {
        const abort = new AbortController();
        controller = abort;
        const connectTimer = scheduler.setTimeout(() => {
          abort.abort();
        }, connectTimeoutMs);
        let response: Response;
        try {
          response = await doFetch(url, {
            method: 'GET',
            headers: { accept: 'text/event-stream', ...authHeaders(token, astronautId) },
            redirect: 'error',
            signal: abort.signal,
          });
        } catch {
          return LOST;
        } finally {
          // From here on the answer has arrived: only silence is limited, not the total time.
          scheduler.clearTimeout(connectTimer);
        }
        if (closed) {
          discard(response);
          return LOST;
        }

        const finalError = (code: 'not_found' | 'no_reps' | 'unavailable', cause?: unknown): Attempt => {
          discard(response);
          if (cause !== undefined) logError(cause);
          emit({ kind: 'error', code });
          return FINAL;
        };
        if (response.status === 404) return finalError('not_found');
        if (response.status === 409) return finalError('no_reps');
        // 408 (request timeout) and 429 (too many requests) are passing conditions: retried like a 5xx.
        if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
          return finalError('unavailable', new Error(`reps stream refused with status ${String(response.status)}`));
        }
        if (response.status !== 200 || response.body === null) {
          discard(response);
          return LOST;
        }
        if (!(response.headers.get('content-type') ?? '').toLowerCase().startsWith('text/event-stream')) {
          return finalError('unavailable', new Error('reps stream answered a content type that is not an event stream'));
        }

        const reader: ReadableStreamDefaultReader<Uint8Array> = response.body.getReader();
        abort.signal.addEventListener(
          'abort',
          () => {
            void reader.cancel().catch(() => undefined);
          },
          { once: true },
        );
        const decoder = new TextDecoder('utf-8');
        // Mutated from the parser callback, so kept in an object (a plain `let` would be narrowed as constant).
        const progress = { final: false, gotState: false };
        let idleTimer: unknown;
        const armIdle = (): void => {
          scheduler.clearTimeout(idleTimer);
          idleTimer = scheduler.setTimeout(() => {
            abort.abort();
          }, idleTimeoutMs);
        };
        const fail = (cause: unknown): void => {
          progress.final = true;
          logError(cause);
          emit({ kind: 'error', code: 'unavailable' });
        };
        const parser = createSseParser((sse) => {
          if (progress.final || closed || (sse.event !== 'state' && sse.event !== 'end')) return;
          let parsed;
          try {
            parsed = repStateSchema.safeParse(JSON.parse(sse.data));
          } catch (error) {
            fail(error);
            return;
          }
          if (!parsed.success) {
            fail(parsed.error);
            return;
          }
          if (parsed.data.session_id !== sessionId) {
            fail(new Error('reps event of another session'));
            return;
          }
          progress.gotState = true;
          if (sse.event === 'end') progress.final = true;
          emit({ kind: sse.event, state: parsed.data });
        });

        armIdle();
        try {
          while (!progress.final) {
            let chunk: Awaited<ReturnType<typeof reader.read>>;
            try {
              chunk = await reader.read();
            } catch {
              break;
            }
            if (chunk.done) break;
            armIdle();
            try {
              parser.push(decoder.decode(chunk.value, { stream: true }));
            } catch (error) {
              fail(error);
            }
          }
        } finally {
          scheduler.clearTimeout(idleTimer);
          abort.abort();
        }
        return { outcome: progress.final ? 'final' : 'lost', gotState: progress.gotState };
      }

      function sleep(ms: number): Promise<void> {
        return new Promise((resolve) => {
          wake = resolve;
          retryTimer = scheduler.setTimeout(() => {
            wake = null;
            resolve();
          }, ms);
        });
      }

      async function run(): Promise<void> {
        let attempt = 0;
        let lostReported = false;
        while (!isClosed()) {
          const result = await connect();
          if (isClosed() || result.outcome === 'final') return;
          if (result.gotState) {
            attempt = 0;
            lostReported = false;
          }
          if (!lostReported) {
            lostReported = true;
            emit({ kind: 'lost' });
          }
          const delay = backoffMs[Math.min(attempt, backoffMs.length - 1)] ?? 5_000;
          attempt += 1;
          await sleep(delay);
        }
      }

      void run().catch(logError);

      return () => {
        if (closed) return;
        closed = true;
        controller?.abort();
        scheduler.clearTimeout(retryTimer);
        wake?.();
      };
    },
  };
}
