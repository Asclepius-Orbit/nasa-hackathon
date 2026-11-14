import { describe, expect, it } from 'vitest';

import type { ChatEvent } from '../../shared/api';
import type { Scheduler } from '../views/reps-stream';

import { createChatStreamClient, type ChatFetch, type ChatStreamOptions } from './chat-stream';

const BASE = 'http://127.0.0.1:8003';
const CONVERSATION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const V = { schema_version: '1.0.0' };
const frame = (type: string, rest: object = {}): string => `event: ${type}\ndata: ${JSON.stringify({ ...V, type, ...rest })}\n\n`;
const TEXT = frame('text', { delta: 'Hello ' });
const END = frame('end');

const flush = async (): Promise<void> => {
  for (let i = 0; i < 5; i += 1) await new Promise<void>((resolve) => setImmediate(resolve));
};

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

type Init = Parameters<ChatFetch>[1];

function liveStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const encoder = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
    push: (text: string) => {
      controller.enqueue(encoder.encode(text));
    },
    close: () => {
      controller.close();
    },
  };
}
const problem = (status: number) =>
  new Response(JSON.stringify({ type: 'x', title: 'x', status }), { status, headers: { 'content-type': 'application/problem+json' } });
const hang = (signal: AbortSignal) =>
  new Promise<Response>((_resolve, reject) => {
    signal.addEventListener('abort', () => {
      reject(new DOMException('aborted', 'AbortError'));
    });
  });

function setup(options: Partial<ChatStreamOptions> = {}) {
  const calls: { url: string; init: Init }[] = [];
  const handlers: ((signal: AbortSignal) => Promise<Response> | Response)[] = [];
  const clock = new FakeScheduler();
  const events: ChatEvent[] = [];
  const logged: unknown[] = [];
  const client = createChatStreamClient({
    baseUrl: BASE,
    token: 'tok',
    scheduler: clock,
    logError: (error) => logged.push(error),
    fetch: (url, init) => {
      calls.push({ url, init });
      return Promise.resolve().then(() => {
        const handler = handlers.shift();
        if (handler === undefined) throw new TypeError('fetch failed');
        return handler(init.signal);
      });
    },
    ...options,
  });
  const ask = (question = 'How am I?') =>
    client.ask({ conversationId: CONVERSATION, question, astronautId: 'AST-01' }, (event) => {
      events.push(event);
    });
  return { calls, handlers, clock, events, logged, client, ask };
}

describe('ask: request', () => {
  it('posts the contract body with the credentials, asking for an event stream, without redirects', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask('How am I?');
    await flush();
    expect(s.calls).toHaveLength(1);
    expect(s.calls[0]?.url).toBe(`${BASE}/chat`);
    const init = s.calls[0]?.init;
    expect(init?.method).toBe('POST');
    expect(init?.redirect).toBe('error');
    expect(init?.headers).toMatchObject({
      accept: 'text/event-stream',
      'content-type': 'application/json',
      authorization: 'Bearer tok',
      'x-astronaut-id': 'AST-01',
    });
    expect(JSON.parse(init?.body as string)).toEqual({
      schema_version: '1.0.0',
      astronaut_id: 'AST-01',
      conversation_id: CONVERSATION,
      question: 'How am I?',
    });
  });

  it('refuses a backend that is not loopback', () => {
    expect(() => createChatStreamClient({ baseUrl: 'http://example.com' })).toThrow(/loopback/);
  });
});

describe('ask: events', () => {
  it('maps every event of the contract and drops the tool arguments', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    stream.push(frame('tool_call', { tool_name: 'get_current_status', tool_args: { secret: 'x' } }));
    stream.push(TEXT);
    stream.push(frame('notice', { code: 'correction', text: 'Reworded.' }));
    stream.push(END);
    await flush();
    expect(s.events).toEqual([
      { kind: 'tool_call', toolName: 'get_current_status' },
      { kind: 'text', delta: 'Hello ' },
      { kind: 'notice', code: 'correction', text: 'Reworded.' },
      { kind: 'end' },
    ]);
    expect(JSON.stringify(s.events)).not.toContain('secret');
  });

  it('maps fallback, mid_stream notice and an error event', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    stream.push(frame('notice', { code: 'mid_stream', text: 'Model failed.' }));
    stream.push(frame('fallback', { text: 'Status: amber' }));
    stream.push(frame('error', { code: 'internal', message: 'boom' }));
    await flush();
    expect(s.events).toEqual([
      { kind: 'notice', code: 'mid_stream', text: 'Model failed.' },
      { kind: 'fallback', text: 'Status: amber' },
      { kind: 'error', code: 'internal' },
    ]);
  });

  it('reads events cut across chunks and ignores ping comments', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    stream.push(': ping\n\n');
    stream.push(TEXT.slice(0, 20));
    await flush();
    stream.push(TEXT.slice(20) + END);
    await flush();
    expect(s.events).toEqual([{ kind: 'text', delta: 'Hello ' }, { kind: 'end' }]);
  });

  it('delivers nothing after end', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    stream.push(END + TEXT);
    await flush();
    expect(s.events).toEqual([{ kind: 'end' }]);
  });

  it('isolates a throwing callback', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.client.ask({ conversationId: CONVERSATION, question: 'q', astronautId: 'AST-01' }, () => {
      throw new Error('renderer bug');
    });
    await flush();
    stream.push(TEXT + END);
    await flush();
    expect(s.logged).toHaveLength(2);
  });
});

describe('ask: failures end with one error', () => {
  it.each([
    [401, 'locked'],
    [403, 'locked'],
    [409, 'busy'],
    [422, 'invalid'],
    [503, 'unavailable'],
    [500, 'unavailable'],
    [404, 'unavailable'],
  ] as const)('answers %i with %s before any stream', async (status, code) => {
    const s = setup();
    s.handlers.push(() => problem(status));
    s.ask();
    await flush();
    expect(s.events).toEqual([{ kind: 'error', code }]);
    expect(s.calls).toHaveLength(1);
  });

  it('reports a network failure as unavailable, without retrying', async () => {
    const s = setup();
    s.ask();
    await flush();
    expect(s.events).toEqual([{ kind: 'error', code: 'unavailable' }]);
    expect(s.calls).toHaveLength(1);
  });

  it('refuses a 200 that is not an event stream', async () => {
    const s = setup();
    s.handlers.push(() => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }));
    s.ask();
    await flush();
    expect(s.events).toEqual([{ kind: 'error', code: 'unavailable' }]);
  });

  it('treats a cut stream (closed without end) as unavailable, keeping the text already shown', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    stream.push(TEXT);
    stream.close();
    await flush();
    expect(s.events).toEqual([{ kind: 'text', delta: 'Hello ' }, { kind: 'error', code: 'unavailable' }]);
  });

  it.each([
    ['not JSON', 'event: text\ndata: {nope\n\n'],
    ['outside the contract', frame('text', { delta: '' })],
    ['an unknown type', frame('mystery')],
    ['a name that differs from its type', `event: end\ndata: ${JSON.stringify({ ...V, type: 'text', delta: 'x' })}\n\n`],
    ['another major version', `event: end\ndata: ${JSON.stringify({ schema_version: '2.0.0', type: 'end' })}\n\n`],
    ['a tool call with no arguments object', frame('tool_call', { tool_name: 'x', tool_args: 'no' })],
  ])('stops with unavailable on an event %s', async (_name, bad) => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    stream.push(bad + TEXT);
    await flush();
    expect(s.events).toEqual([{ kind: 'error', code: 'unavailable' }]);
    expect(s.logged).toHaveLength(1);
  });

  it('stops with unavailable when one event is far too large', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    stream.push(`data: ${'x'.repeat(70_000)}`);
    await flush();
    expect(s.events).toEqual([{ kind: 'error', code: 'unavailable' }]);
  });
});

describe('ask: time', () => {
  it('gives up when the connection does not answer in 10 s', async () => {
    const s = setup();
    s.handlers.push(hang);
    s.ask();
    await flush();
    await s.clock.advance(9_999);
    expect(s.events).toEqual([]);
    await s.clock.advance(1);
    expect(s.events).toEqual([{ kind: 'error', code: 'unavailable' }]);
  });

  it('waits up to 120 s of silence for the first token, and pings restart the count', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    await s.clock.advance(100_000);
    stream.push(': ping\n\n');
    await flush();
    await s.clock.advance(100_000);
    expect(s.events).toEqual([]);
    stream.push(TEXT);
    await flush();
    expect(s.events).toEqual([{ kind: 'text', delta: 'Hello ' }]);
  });

  it('reports 120 s of silence as unavailable, without a retry', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    s.ask();
    await flush();
    await s.clock.advance(120_000);
    expect(s.events).toEqual([{ kind: 'error', code: 'unavailable' }]);
    expect(s.calls).toHaveLength(1);
    expect(s.clock.pending).toBe(0);
  });
});

describe('ask: cancel', () => {
  it('aborts the connection and stays silent, any number of times', async () => {
    const s = setup();
    const stream = liveStream();
    let signal: AbortSignal | undefined;
    s.handlers.push((sig) => {
      signal = sig;
      return stream.response;
    });
    const cancel = s.ask();
    await flush();
    stream.push(TEXT);
    await flush();
    cancel();
    cancel();
    await flush();
    expect(signal?.aborted).toBe(true);
    expect(s.events).toEqual([{ kind: 'text', delta: 'Hello ' }]);
    expect(s.clock.pending).toBe(0);
  });

  it('cancelling before the connection answers aborts it and discards the answer', async () => {
    const s = setup();
    const stream = liveStream();
    s.handlers.push(() => stream.response);
    const cancel = s.ask();
    cancel();
    await flush();
    expect(s.events).toEqual([]);
    expect(() => {
      stream.push(TEXT);
    }).toThrow();
  });
});

describe('forget', () => {
  it('sends DELETE with the credentials and resolves', async () => {
    const s = setup();
    s.handlers.push(() => new Response(null, { status: 204 }));
    await s.client.forget(CONVERSATION, 'AST-01');
    expect(s.calls[0]?.url).toBe(`${BASE}/chat/${CONVERSATION}`);
    expect(s.calls[0]?.init.method).toBe('DELETE');
    expect(s.calls[0]?.init.headers).toMatchObject({ authorization: 'Bearer tok', 'x-astronaut-id': 'AST-01' });
  });

  it('never rejects: a refusal or a network failure is logged', async () => {
    const s = setup();
    s.handlers.push(() => problem(500));
    await expect(s.client.forget(CONVERSATION, 'AST-01')).resolves.toBeUndefined();
    await expect(s.client.forget(CONVERSATION, 'AST-01')).resolves.toBeUndefined();
    expect(s.logged).toHaveLength(2);
  });

  it('gives up when the agent does not answer in 10 s', async () => {
    const s = setup();
    s.handlers.push(hang);
    const done = s.client.forget(CONVERSATION, 'AST-01');
    await flush();
    await s.clock.advance(10_000);
    await expect(done).resolves.toBeUndefined();
  });
});
