import { describe, expect, it } from 'vitest';

import type { RepsEvent } from '../../shared/api';
import { createRepsStreamClient, type RepsStreamOptions, type Scheduler } from './reps-stream';
import type { ViewFetch } from './views-client';

const BASE = 'http://127.0.0.1:8000';
const SESSION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const STATE = {
  schema_version: '1.0.0',
  session_id: SESSION,
  rep: 1,
  total: 60,
  set: 1,
  sets_total: 4,
  reps_per_set: 15,
  rep_in_set: 1,
  phase: 'pulling',
  progress_pct: 20,
  last: null,
  signal: 'ok',
  as_of: '2026-11-08T10:00:00Z',
} as const;
const frame = (event: string, state: object = STATE): string => `event: ${event}\ndata: ${JSON.stringify(state)}\n\n`;
const SSE_HEADERS = { 'content-type': 'text/event-stream' };

/** Let promise continuations run; the fake scheduler never needs real time. */
const flush = async (): Promise<void> => {
  for (let i = 0; i < 5; i += 1) await new Promise<void>((resolve) => setImmediate(resolve));
};

/** Hand-written clock: timers only fire when the test advances time. */
class FakeScheduler implements Scheduler {
  now = 0;
  private nextId = 1;
  private timers = new Map<number, { at: number; fn: () => void }>();
  setTimeout(fn: () => void, ms: number): number {
    const id = this.nextId++;
    this.timers.set(id, { at: this.now + ms, fn });
    return id;
  }
  clearTimeout(handle: unknown): void {
    this.timers.delete(handle as number);
  }
  get pending(): number {
    return this.timers.size;
  }
  async advance(ms: number): Promise<void> {
    const target = this.now + ms;
    for (;;) {
      const due = [...this.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (due === undefined) break;
      this.timers.delete(due[0]);
      this.now = due[1].at;
      due[1].fn();
      await flush();
    }
    this.now = target;
    await flush();
  }
}

type Init = Parameters<ViewFetch>[1];

/** Hand-written fake of the HTTP boundary: answers each connection with the next queued handler. */
class FakeBackend {
  calls: { url: string; init: Init }[] = [];
  private handlers: ((signal: AbortSignal) => Promise<Response> | Response)[] = [];
  next(handler: (signal: AbortSignal) => Promise<Response> | Response): this {
    this.handlers.push(handler);
    return this;
  }
  fetch: ViewFetch = (url, init) => {
    this.calls.push({ url, init });
    // Picked one tick later, so a test may queue its answers right after starting the stream.
    return Promise.resolve().then(() => {
      const handler = this.handlers.shift();
      if (handler === undefined) throw new TypeError('fetch failed');
      return handler(init.signal);
    });
  };
}

/** An SSE response the test feeds by hand. */
function liveStream(status = 200, contentType = 'text/event-stream') {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const encoder = new TextEncoder();
  return {
    response: new Response(body, { status, headers: { 'content-type': contentType } }),
    push: (text: string) => {
      controller.enqueue(encoder.encode(text));
    },
    close: () => {
      controller.close();
    },
  };
}
const respond = (stream: ReturnType<typeof liveStream>) => () => stream.response;
const problem = (status: number) => () =>
  new Response(JSON.stringify({ type: 'x', title: 'x', status }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });
const hang = (signal: AbortSignal) =>
  new Promise<Response>((_resolve, reject) => {
    signal.addEventListener('abort', () => {
      reject(new DOMException('aborted', 'AbortError'));
    });
  });

function setup(options: Partial<RepsStreamOptions> = {}) {
  const backend = new FakeBackend();
  const clock = new FakeScheduler();
  const events: RepsEvent[] = [];
  const logged: unknown[] = [];
  const client = createRepsStreamClient({
    baseUrl: BASE,
    fetch: backend.fetch,
    scheduler: clock,
    logError: (error) => logged.push(error),
    ...options,
  });
  const close = client.open(SESSION, (event) => events.push(event));
  return { backend, clock, events, logged, close };
}
const kinds = (events: RepsEvent[]) => events.map((event) => event.kind);

describe('createRepsStreamClient', () => {
  it('refuses a backend that is not loopback', () => {
    expect(() => createRepsStreamClient({ baseUrl: 'http://example.com' })).toThrow(/loopback/u);
  });

  it('asks for the event stream of that session without following redirects and relays each event', async () => {
    const live = liveStream();
    const { backend, events } = setup();
    backend.next(respond(live));
    await flush();
    expect(backend.calls).toHaveLength(1);
    expect(backend.calls[0]?.url).toBe(`${BASE}/ui/sessions/${SESSION}/reps`);
    expect(backend.calls[0]?.init).toMatchObject({
      method: 'GET',
      headers: { accept: 'text/event-stream' },
      redirect: 'error',
    });

    const text = frame('state') + frame('state', { ...STATE, rep: 2 });
    live.push(text.slice(0, 30));
    live.push(text.slice(30));
    await flush();
    expect(events).toEqual([
      { kind: 'state', state: STATE },
      { kind: 'state', state: { ...STATE, rep: 2 } },
    ]);
  });

  it('delivers end, stops, and never reconnects after it', async () => {
    const live = liveStream();
    const { backend, clock, events } = setup();
    backend.next(respond(live));
    await flush();
    live.push(frame('state') + frame('end', { ...STATE, rep: 10 }));
    live.close();
    await flush();
    expect(kinds(events)).toEqual(['state', 'end']);
    await clock.advance(60_000);
    expect(backend.calls).toHaveLength(1);
    expect(clock.pending).toBe(0);
  });

  it.each([
    [404, 'not_found'],
    [409, 'no_reps'],
    [422, 'unavailable'],
    [400, 'unavailable'],
  ])('answers %i with a final error %s and no retry', async (status, code) => {
    const { backend, clock, events } = setup();
    backend.next(problem(status));
    await flush();
    await clock.advance(60_000);
    expect(events).toEqual([{ kind: 'error', code }]);
    expect(backend.calls).toHaveLength(1);
  });

  it.each([408, 429])('answers %i as a passing hiccup: one lost, then it reconnects', async (status) => {
    const live = liveStream();
    const { backend, clock, events } = setup();
    backend.next(problem(status)).next(respond(live));
    await flush();
    expect(events).toEqual([{ kind: 'lost' }]);
    await clock.advance(1000);
    expect(backend.calls).toHaveLength(2);
    live.push(frame('state'));
    await flush();
    expect(kinds(events)).toEqual(['lost', 'state']);
  });

  it.each([
    ['not an event stream', () => new Response('x', { headers: { 'content-type': 'text/html' } })],
    ['an invalid state', () => new Response(frame('state', { ...STATE, rep: -1 }), { headers: SSE_HEADERS })],
    [
      'a state of another session',
      () =>
        new Response(frame('state', { ...STATE, session_id: 'aaaaaaaa-5d4a-4e6f-9a7b-1c2d3e4f5a6b' }), {
          headers: SSE_HEADERS,
        }),
    ],
    ['data that is not JSON', () => new Response('event: end\ndata: {nope\n\n', { headers: SSE_HEADERS })],
  ])('ends in a final error when the backend answers %s', async (_label, handler) => {
    const { backend, clock, events, logged } = setup();
    backend.next(handler);
    await flush();
    await clock.advance(60_000);
    expect(events).toEqual([{ kind: 'error', code: 'unavailable' }]);
    expect(backend.calls).toHaveLength(1);
    expect(logged).toHaveLength(1);
  });

  it('ignores event names it does not know', async () => {
    const live = liveStream();
    const { backend, events } = setup();
    backend.next(respond(live));
    await flush();
    live.push('event: future\ndata: {}\n\n: ping\n\n' + frame('state'));
    await flush();
    expect(kinds(events)).toEqual(['state']);
  });

  it('reports one lost, then retries with a growing, capped backoff while the backend is down', async () => {
    const { backend, clock, events } = setup();
    for (let i = 0; i < 5; i += 1) backend.next(problem(503));
    await flush();
    expect(backend.calls).toHaveLength(1);
    expect(events).toEqual([{ kind: 'lost' }]);
    await clock.advance(999);
    expect(backend.calls).toHaveLength(1);
    await clock.advance(1);
    expect(backend.calls).toHaveLength(2);
    await clock.advance(2000);
    expect(backend.calls).toHaveLength(3);
    await clock.advance(5000);
    expect(backend.calls).toHaveLength(4);
    await clock.advance(4999);
    expect(backend.calls).toHaveLength(4);
    await clock.advance(1);
    expect(backend.calls).toHaveLength(5);
    expect(events).toEqual([{ kind: 'lost' }]);
  });

  it('retries when the stream drops without end, resumes on the resent state, and starts the backoff over', async () => {
    const first = liveStream();
    const second = liveStream();
    const { backend, clock, events } = setup();
    backend.next(respond(first)).next(problem(503)).next(respond(second)).next(problem(503));
    await flush();
    first.push(frame('state'));
    first.close();
    await flush();
    expect(kinds(events)).toEqual(['state', 'lost']);
    await clock.advance(1000);
    expect(backend.calls).toHaveLength(2);
    await clock.advance(2000);
    expect(backend.calls).toHaveLength(3);
    second.push(frame('state', { ...STATE, rep: 3 }));
    await flush();
    expect(kinds(events)).toEqual(['state', 'lost', 'state']);
    second.close();
    await flush();
    expect(kinds(events)).toEqual(['state', 'lost', 'state', 'lost']);
    await clock.advance(1000);
    expect(backend.calls).toHaveLength(4);
  });

  it('gives up on a connection that does not answer within 5 s, and retries', async () => {
    const { backend, clock, events } = setup();
    backend.next(hang).next(problem(503));
    await flush();
    await clock.advance(4999);
    expect(events).toEqual([]);
    await clock.advance(1);
    expect(backend.calls[0]?.init.signal.aborted).toBe(true);
    expect(events).toEqual([{ kind: 'lost' }]);
    await clock.advance(1000);
    expect(backend.calls).toHaveLength(2);
  });

  it('has no total time limit: a stream that keeps talking is never cut', async () => {
    const live = liveStream();
    const { backend, clock, events } = setup();
    backend.next(respond(live));
    await flush();
    for (let i = 0; i < 40; i += 1) {
      live.push(': keep-alive\n\n');
      await clock.advance(10_000);
    }
    live.push(frame('state'));
    await flush();
    expect(backend.calls).toHaveLength(1);
    expect(backend.calls[0]?.init.signal.aborted).toBe(false);
    expect(events).toEqual([{ kind: 'state', state: STATE }]);
  });

  it('cuts a stream that goes silent for the idle time, reports lost and reconnects', async () => {
    const live = liveStream();
    const { backend, clock, events } = setup({ idleTimeoutMs: 30_000 });
    backend.next(respond(live)).next(problem(503));
    await flush();
    live.push(frame('state'));
    await flush();
    await clock.advance(29_999);
    expect(kinds(events)).toEqual(['state']);
    await clock.advance(1);
    expect(backend.calls[0]?.init.signal.aborted).toBe(true);
    expect(kinds(events)).toEqual(['state', 'lost']);
    await clock.advance(1000);
    expect(backend.calls).toHaveLength(2);
  });

  it('close() aborts the request, silences the stream and cancels a pending retry', async () => {
    const live = liveStream();
    const { backend, events, close } = setup();
    backend.next(respond(live));
    await flush();
    close();
    expect(backend.calls[0]?.init.signal.aborted).toBe(true);
    await flush();
    expect(events).toEqual([]);

    const retrying = setup();
    retrying.backend.next(problem(503));
    await flush();
    expect(retrying.events).toEqual([{ kind: 'lost' }]);
    retrying.close();
    expect(retrying.clock.pending).toBe(0);
    await retrying.clock.advance(60_000);
    expect(retrying.backend.calls).toHaveLength(1);
  });

  it('close() before the connection answers also stops it', async () => {
    const { backend, events, close } = setup();
    backend.next(hang);
    await flush();
    close();
    await flush();
    expect(events).toEqual([]);
    expect(backend.calls).toHaveLength(1);
  });
});
