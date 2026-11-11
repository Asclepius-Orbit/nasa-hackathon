/**
 * Client of the spine agent's chat (`POST /chat`, `DELETE /chat/{id}`; contract: spine_monitor/contracts/agent-openapi.yaml).
 * Runs in the main process only. It does not know who may talk: the unlocked astronaut is checked by `chat-service.ts`
 * and sent here with each call.
 *
 * Like the views clients it refuses non-loopback agents and redirects, and uses the Node global `fetch` (no system proxy).
 * Each server-sent event is checked against the contract (`agent/chat-event`) and mapped to the renderer's `ChatEvent`;
 * the model's tool arguments are dropped here and never leave main. An event outside the contract ends the answer with
 * `unavailable`: it is never forwarded.
 *
 * Timing: the connection must answer within `connectTimeoutMs`; after that only silence counts (`idleTimeoutMs`, restarted
 * by any bytes, `: ping` comments included). The first token can take a minute on a CPU, so the idle limit is generous.
 * There is deliberately NO reconnection or retry: a chat turn is not idempotent, re-asking is the user's call. A stream that
 * closes without `end` or `error` is a cut answer and ends as `unavailable`.
 */
import { z } from 'zod';

import type { ChatErrorCode, ChatEvent } from '../../shared/api';
import { authHeaders, type AuthHeaders } from '../views/backend-auth';
import { parseBackendUrl } from '../views/backend-url';
import { createSseParser } from '../views/sse-parser';
import type { Scheduler } from '../views/reps-stream';

export const DEFAULT_CONNECT_TIMEOUT_MS = 10_000;
export const DEFAULT_IDLE_TIMEOUT_MS = 120_000;

const SYSTEM_SCHEDULER: Scheduler = {
  setTimeout: (callback, ms) => globalThis.setTimeout(callback, ms),
  clearTimeout: (handle) => {
    globalThis.clearTimeout(handle as ReturnType<typeof globalThis.setTimeout>);
  },
};

/** The part of `fetch` used here, so tests can pass a hand-written fake. */
export type ChatFetch = (
  url: string,
  init: {
    method: 'POST' | 'DELETE';
    headers: Record<string, string> & AuthHeaders;
    body?: string;
    redirect: 'error';
    signal: AbortSignal;
  },
) => Promise<Response>;

export interface ChatStreamOptions {
  /** Origin of the agent; must be loopback (see `parseBackendUrl`). */
  baseUrl: string;
  fetch?: ChatFetch;
  scheduler?: Scheduler;
  connectTimeoutMs?: number;
  idleTimeoutMs?: number;
  /** Where the technical cause of a failed answer goes (it never reaches the renderer). */
  logError?: (error: unknown) => void;
  /** Bearer token of the agent (`backend-auth`); null sends none. */
  token?: string | null;
}

export interface ChatAskInput {
  conversationId: string;
  question: string;
  /** The unlocked astronaut: sent in the body and as `X-Astronaut-Id`. */
  astronautId: string;
}

export interface ChatStreamClient {
  /**
   * Asks a question and returns the function that cancels the answer (closes the connection; idempotent, silent).
   * `onEvent` gets the events in order and, last, exactly one `end` or `error`, never anything after it.
   */
  ask(input: ChatAskInput, onEvent: (event: ChatEvent) => void): () => void;
  /** Forgets a conversation on the agent. Best effort: never rejects. */
  forget(conversationId: string, astronautId: string): Promise<void>;
}

const version = z.string().regex(/^1\.\d+\.\d+$/u);
const nonEmpty = z.string().min(1);

/** Fields the contract does not define are ignored, never forwarded (a later minor may add some). */
const chatEventSchema = z.discriminatedUnion('type', [
  z.looseObject({ schema_version: version, type: z.literal('text'), delta: nonEmpty }),
  z.looseObject({ schema_version: version, type: z.literal('tool_call'), tool_name: nonEmpty, tool_args: z.record(z.string(), z.unknown()) }),
  z.looseObject({ schema_version: version, type: z.literal('fallback'), text: nonEmpty }),
  z.looseObject({ schema_version: version, type: z.literal('notice'), code: z.enum(['mid_stream', 'correction']), text: nonEmpty }),
  z.looseObject({ schema_version: version, type: z.literal('end') }),
  z.looseObject({ schema_version: version, type: z.literal('error'), code: z.enum(['busy', 'unavailable', 'invalid', 'internal']), message: nonEmpty }),
]);

/** What the agent says before opening a stream, by status (problem+json: only the status is read). */
function refusalCode(status: number): ChatErrorCode {
  if (status === 401 || status === 403) return 'locked';
  if (status === 409) return 'busy';
  if (status === 422) return 'invalid';
  return 'unavailable';
}

function discard(response: Response): void {
  void response.body?.cancel().catch(() => undefined);
}

export function createChatStreamClient({
  baseUrl,
  fetch: doFetch = globalThis.fetch,
  scheduler = SYSTEM_SCHEDULER,
  connectTimeoutMs = DEFAULT_CONNECT_TIMEOUT_MS,
  idleTimeoutMs = DEFAULT_IDLE_TIMEOUT_MS,
  logError = () => undefined,
  token = null,
}: ChatStreamOptions): ChatStreamClient {
  const origin = parseBackendUrl(baseUrl);
  if (origin === null) throw new Error('agent URL must be an http(s) loopback origin');
  const base: string = origin;

  return {
    ask({ conversationId, question, astronautId }, onEvent) {
      let closed = false;
      let done = false;
      const abort = new AbortController();

      // `done` and `closed` change while `await`s are pending; a function keeps the compiler from assuming they did not.
      const isOver = (): boolean => done || closed;

      function emit(event: ChatEvent): void {
        if (closed || done) return;
        if (event.kind === 'end' || event.kind === 'error') done = true;
        try {
          onEvent(event);
        } catch (error) {
          logError(error);
        }
      }
      function fail(code: ChatErrorCode, cause?: unknown): void {
        if (closed || done) return;
        if (cause !== undefined) logError(cause);
        emit({ kind: 'error', code });
      }

      async function run(): Promise<void> {
        const connectTimer = scheduler.setTimeout(() => {
          abort.abort();
        }, connectTimeoutMs);
        let response: Response;
        try {
          response = await doFetch(`${base}/chat`, {
            method: 'POST',
            headers: {
              accept: 'text/event-stream',
              'content-type': 'application/json',
              ...authHeaders(token, astronautId),
            },
            body: JSON.stringify({ schema_version: '1.0.0', astronaut_id: astronautId, conversation_id: conversationId, question }),
            redirect: 'error',
            signal: abort.signal,
          });
        } catch (error) {
          fail('unavailable', error);
          return;
        } finally {
          // From here on the answer has arrived: only silence is limited, not the total time.
          scheduler.clearTimeout(connectTimer);
        }
        if (closed) {
          discard(response);
          return;
        }
        if (response.status !== 200) {
          discard(response);
          const code = refusalCode(response.status);
          fail(code, code === 'unavailable' ? new Error(`chat refused with status ${String(response.status)}`) : undefined);
          return;
        }
        if (response.body === null || !(response.headers.get('content-type') ?? '').toLowerCase().startsWith('text/event-stream')) {
          discard(response);
          fail('unavailable', new Error('chat answered something that is not an event stream'));
          return;
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
        let idleTimer: unknown;
        const armIdle = (): void => {
          scheduler.clearTimeout(idleTimer);
          idleTimer = scheduler.setTimeout(() => {
            abort.abort();
          }, idleTimeoutMs);
        };
        const parser = createSseParser((sse) => {
          if (done || closed) return;
          let parsed;
          try {
            parsed = chatEventSchema.safeParse(JSON.parse(sse.data));
          } catch (error) {
            fail('unavailable', error);
            return;
          }
          if (!parsed.success) {
            fail('unavailable', parsed.error);
            return;
          }
          const data = parsed.data;
          if (sse.event !== data.type) {
            fail('unavailable', new Error('chat event name differs from its type'));
            return;
          }
          switch (data.type) {
            case 'text':
              emit({ kind: 'text', delta: data.delta });
              break;
            case 'tool_call':
              emit({ kind: 'tool_call', toolName: data.tool_name });
              break;
            case 'fallback':
              emit({ kind: 'fallback', text: data.text });
              break;
            case 'notice':
              emit({ kind: 'notice', code: data.code, text: data.text });
              break;
            case 'end':
              emit({ kind: 'end' });
              break;
            case 'error':
              emit({ kind: 'error', code: data.code });
              break;
          }
        });

        armIdle();
        try {
          while (!isOver()) {
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
              fail('unavailable', error);
            }
          }
        } finally {
          scheduler.clearTimeout(idleTimer);
          abort.abort();
        }
        // Closed, silent or cut before `end`/`error`: the answer is incomplete.
        fail('unavailable', new Error('chat stream ended without end'));
      }

      void run().catch(logError);

      return () => {
        if (closed) return;
        closed = true;
        abort.abort();
      };
    },

    async forget(conversationId, astronautId) {
      const abort = new AbortController();
      const timer = scheduler.setTimeout(() => {
        abort.abort();
      }, connectTimeoutMs);
      try {
        const response = await doFetch(`${base}/chat/${encodeURIComponent(conversationId)}`, {
          method: 'DELETE',
          headers: { ...authHeaders(token, astronautId) },
          redirect: 'error',
          signal: abort.signal,
        });
        discard(response);
        if (response.status !== 204) logError(new Error(`forget refused with status ${String(response.status)}`));
      } catch (error) {
        logError(error);
      } finally {
        scheduler.clearTimeout(timer);
      }
    },
  };
}
