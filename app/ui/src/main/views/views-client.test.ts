import { describe, expect, it } from 'vitest';

import { createViewsClient, type ViewFetch, type ViewsClient } from './views-client';
import { ViewsError } from './views-error';

const BASE = 'http://127.0.0.1:8000';
const ASTRONAUT = 'AST-01';
const SESSION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';

const baseline = {
  schema_version: '1.0.0',
  recorded: 1,
  required: 3,
  reference_version: null,
  built_from_sessions: 0,
  sessions: [],
};
const BODIES = {
  strip: { schema_version: '1.0.0', astronaut_id: ASTRONAUT, mission: { day: 3, start: null }, harness: null },
  today: { schema_version: '1.0.0', level: 'nodata', recommendations: [], reasons: [] },
  history: { schema_version: '1.1.0', from: '2026-11-01', to: '2026-11-07', days: [] },
  baseline,
  firstDay: { schema_version: '1.0.0', todo: ['pair_harness'], baseline },
  houston: { schema_version: '1.0.0', astronaut_id: ASTRONAUT, summary: null, ground_link: null },
  outbox: { schema_version: '1.0.0', astronaut_id: ASTRONAUT, ground_link: null, items: [] },
  session: {
    schema_version: '1.0.0',
    session_id: SESSION,
    astronaut_id: ASTRONAUT,
    state: 'created',
    protocol_id: 'stretch.lumbar',
  },
};

interface Call {
  url: string;
  init: Parameters<ViewFetch>[1];
}

/** Hand-written fake of the HTTP boundary: records calls and answers with a canned `Response`. */
function fakeFetch(respond: () => Promise<Response> | Response): { fetch: ViewFetch; calls: Call[] } {
  const calls: Call[] = [];
  const fetch: ViewFetch = (url, init) => {
    calls.push({ url, init });
    return Promise.resolve(respond());
  };
  return { fetch, calls };
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const problem = (status: number, type = 'internal'): Response =>
  new Response(JSON.stringify({ type, title: 'x', status }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });

async function codeOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ViewsError) return error.code;
    throw error;
  }
  throw new Error('expected a ViewsError');
}

describe('createViewsClient requests', () => {
  const cases: [string, (c: ViewsClient) => Promise<unknown>, string, unknown][] = [
    ['strip', (c) => c.strip(ASTRONAUT), `/ui/crew/${ASTRONAUT}/views/strip`, BODIES.strip],
    ['today', (c) => c.today(ASTRONAUT), `/ui/crew/${ASTRONAUT}/views/today`, BODIES.today],
    ['baseline', (c) => c.baseline(ASTRONAUT), `/ui/crew/${ASTRONAUT}/views/baseline`, BODIES.baseline],
    ['first-day', (c) => c.firstDay(ASTRONAUT), `/ui/crew/${ASTRONAUT}/views/first-day`, BODIES.firstDay],
    ['houston', (c) => c.houston(ASTRONAUT), `/ui/crew/${ASTRONAUT}/houston`, BODIES.houston],
    ['outbox', (c) => c.outbox(ASTRONAUT), `/ui/crew/${ASTRONAUT}/outbox`, BODIES.outbox],
    ['session', (c) => c.session(ASTRONAUT, SESSION), `/ui/sessions/${SESSION}/view`, BODIES.session],
    ['history without range', (c) => c.history(ASTRONAUT, {}), `/ui/crew/${ASTRONAUT}/views/history`, BODIES.history],
  ];

  it.each(cases)('GET %s hits the contract path and returns the body untouched', async (_name, call, path, body) => {
    const { fetch, calls } = fakeFetch(() => json(body));
    const result = await call(createViewsClient({ baseUrl: BASE, fetch }));
    expect(result).toEqual(body);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`${BASE}${path}`);
    expect(calls[0]?.init).toMatchObject({ method: 'GET', redirect: 'error', headers: { accept: 'application/json' } });
    expect(calls[0]?.init.signal).toBeInstanceOf(AbortSignal);
  });

  it.each([
    [{ from: '2026-11-01', to: '2026-11-07' }, '?from=2026-11-01&to=2026-11-07'],
    [{ from: '2026-11-01' }, '?from=2026-11-01'],
    [{ to: '2026-11-07' }, '?to=2026-11-07'],
    [{}, ''],
  ])('history range %j becomes the query %j', async (range, query) => {
    const { fetch, calls } = fakeFetch(() => json(BODIES.history));
    await createViewsClient({ baseUrl: BASE, fetch }).history(ASTRONAUT, range);
    expect(calls[0]?.url).toBe(`${BASE}/ui/crew/${ASTRONAUT}/views/history${query}`);
  });

  it('percent-encodes ids so they cannot add path segments or query parameters', async () => {
    const { fetch, calls } = fakeFetch(() => json({}));
    const client = createViewsClient({ baseUrl: BASE, fetch });
    await codeOf(client.strip('AST-01/../x?y=1#z'));
    await codeOf(client.session(ASTRONAUT, 'a/b?c'));
    expect(calls[0]?.url).toBe(`${BASE}/ui/crew/AST-01%2F..%2Fx%3Fy%3D1%23z/views/strip`);
    expect(calls[1]?.url).toBe(`${BASE}/ui/sessions/a%2Fb%3Fc/view`);
  });

  it('refuses a base URL that is not loopback', () => {
    expect(() => createViewsClient({ baseUrl: 'http://example.com:8000' })).toThrow(/loopback/);
    expect(() => createViewsClient({ baseUrl: 'http://127.0.0.1.evil.com' })).toThrow(/loopback/);
  });
});

describe('createViewsClient errors', () => {
  const client = (respond: () => Promise<Response> | Response): ViewsClient =>
    createViewsClient({ baseUrl: BASE, fetch: fakeFetch(respond).fetch });

  it('maps 404 (problem+json) to not_found', async () => {
    expect(await codeOf(client(() => problem(404, 'not_found')).strip(ASTRONAUT))).toBe('not_found');
    expect(await codeOf(client(() => problem(404, 'not_found')).session(ASTRONAUT, SESSION))).toBe('not_found');
  });

  it.each([400, 401, 403, 422, 500, 502, 503])('maps HTTP %i to unavailable', async (status) => {
    expect(await codeOf(client(() => problem(status)).today(ASTRONAUT))).toBe('unavailable');
  });

  it('maps a success status other than 200 to unavailable', async () => {
    expect(await codeOf(client(() => new Response(null, { status: 204 })).today(ASTRONAUT))).toBe('unavailable');
    expect(await codeOf(client(() => new Response(null, { status: 301 })).today(ASTRONAUT))).toBe('unavailable');
  });

  it('maps a network failure to unavailable and keeps the cause for the log', async () => {
    const cause = new TypeError('fetch failed');
    const failing = client(() => Promise.reject(cause));
    const error = await failing.today(ASTRONAUT).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ViewsError);
    expect(error).toMatchObject({ code: 'unavailable', cause });
  });

  it('maps a timeout to unavailable by aborting the request', async () => {
    let aborted = false;
    const hanging: ViewFetch = (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          aborted = true;
          reject(init.signal.reason as Error);
        });
      });
    const slow = createViewsClient({ baseUrl: BASE, fetch: hanging, timeoutMs: 20 });
    expect(await codeOf(slow.today(ASTRONAUT))).toBe('unavailable');
    expect(aborted).toBe(true);
  });

  it('times out while reading a body that never ends too', async () => {
    const stalled: ViewFetch = (_url, init) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          init.signal.addEventListener('abort', () => {
            controller.error(init.signal.reason);
          });
        },
      });
      return Promise.resolve(new Response(body, { status: 200 }));
    };
    const slow = createViewsClient({ baseUrl: BASE, fetch: stalled, timeoutMs: 20 });
    expect(await codeOf(slow.today(ASTRONAUT))).toBe('unavailable');
  });

  it('maps a body that is not JSON to unavailable', async () => {
    expect(await codeOf(client(() => new Response('<html>oops</html>', { status: 200 })).today(ASTRONAUT))).toBe(
      'unavailable',
    );
    expect(await codeOf(client(() => new Response('', { status: 200 })).today(ASTRONAUT))).toBe('unavailable');
  });

  it.each([
    ['null', null],
    ['an array', []],
    ['a string', 'ok'],
    ['an empty object', {}],
    ['a future major', { ...BODIES.today, schema_version: '2.0.0' }],
    ['a malformed version', { ...BODIES.today, schema_version: 'latest' }],
    ['a missing field', { schema_version: '1.0.0', level: 'nodata' }],
    ['a wrongly typed field', { ...BODIES.today, recommendations: 'none' }],
  ])('maps a 200 with %s to unavailable (never invents data)', async (_label, body) => {
    expect(await codeOf(client(() => json(body)).today(ASTRONAUT))).toBe('unavailable');
  });

  it('rejects a view that belongs to someone else or to another session', async () => {
    expect(await codeOf(client(() => json({ ...BODIES.strip, astronaut_id: 'AST-02' })).strip(ASTRONAUT))).toBe(
      'unavailable',
    );
    expect(await codeOf(client(() => json({ ...BODIES.session, session_id: 'other' })).session(ASTRONAUT, SESSION))).toBe(
      'unavailable',
    );
  });

  it('answers not_found for a session that belongs to another astronaut', async () => {
    const foreign = client(() => json({ ...BODIES.session, astronaut_id: 'AST-02' }));
    expect(await codeOf(foreign.session(ASTRONAUT, SESSION))).toBe('not_found');
  });

  it('answers not_found when the backend guard answers 403 for another astronaut session', async () => {
    expect(await codeOf(client(() => problem(403, 'forbidden')).session(ASTRONAUT, SESSION))).toBe('not_found');
  });

  it('rejects a session view that names no astronaut as unavailable', async () => {
    const { astronaut_id: _omitted, ...anonymous } = BODIES.session;
    expect(await codeOf(client(() => json(anonymous)).session(ASTRONAUT, SESSION))).toBe('unavailable');
  });

  const onlyVersion = json({ schema_version: '1.0.0' });
  it.each<[string, (c: ViewsClient) => Promise<unknown>]>([
    ['strip', (c) => c.strip(ASTRONAUT)],
    ['history', (c) => c.history(ASTRONAUT, {})],
    ['baseline', (c) => c.baseline(ASTRONAUT)],
    ['first-day', (c) => c.firstDay(ASTRONAUT)],
    ['session', (c) => c.session(ASTRONAUT, SESSION)],
    ['houston', (c) => c.houston(ASTRONAUT)],
    ['outbox', (c) => c.outbox(ASTRONAUT)],
  ])('validates the %s shape', async (_name, call) => {
    expect(await codeOf(call(client(() => onlyVersion.clone())))).toBe('unavailable');
  });

  it('keeps no cache: after a success, a failure is still an error', async () => {
    let healthy = true;
    const flaky = client(() => (healthy ? json(BODIES.today) : problem(503)));
    expect(await flaky.today(ASTRONAUT)).toEqual(BODIES.today);
    healthy = false;
    expect(await codeOf(flaky.today(ASTRONAUT))).toBe('unavailable');
  });
});

describe('houston and outbox identity', () => {
  it.each<[string, (c: ViewsClient) => Promise<unknown>, unknown]>([
    ['houston', (c) => c.houston(ASTRONAUT), BODIES.houston],
    ['outbox', (c) => c.outbox(ASTRONAUT), BODIES.outbox],
  ])('rejects a %s view of another astronaut as unavailable', async (_name, call, body) => {
    const { fetch } = fakeFetch(() => json({ ...(body as object), astronaut_id: 'AST-02' }));
    expect(await codeOf(call(createViewsClient({ baseUrl: BASE, fetch })))).toBe('unavailable');
  });
});
