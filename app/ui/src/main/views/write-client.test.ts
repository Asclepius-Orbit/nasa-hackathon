import { describe, expect, it } from 'vitest';
import { z } from 'zod';

import { createWriteClient, type WriteFetch } from './write-client';

const BASE = 'http://127.0.0.1:8000';
const ASTRONAUT = 'AST-01';
const DAY = '2026-11-11';

const PROFILE = {
  schema_version: '1.0.0',
  astronaut_id: ASTRONAUT,
  updated_at: '2026-11-11T10:00:00+00:00',
  date_of_birth: '1985-04-02',
  sex_at_birth: 'female',
  anthropometry: { height_standing_cm: 170, body_mass_kg: 62 },
};
const CONTEXT = {
  schema_version: '1.0.0',
  astronaut_id: ASTRONAUT,
  date: DAY,
  phase: 'inflight',
  gravity_environment: 'microgravity',
};

interface Call {
  url: string;
  init: Parameters<WriteFetch>[1];
}

/** Hand-written fake of the HTTP boundary. */
function fakeFetch(respond: () => Promise<Response> | Response) {
  const calls: Call[] = [];
  const fetch: WriteFetch = (url, init) => {
    calls.push({ url, init });
    return Promise.resolve().then(respond);
  };
  return { fetch, calls };
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const problem = (status: number, extra: Record<string, unknown> = {}): Response =>
  new Response(JSON.stringify({ type: 'whatever', title: 'secret backend text', status, ...extra }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });

function build(respond: () => Promise<Response> | Response, token: string | null = 'tok') {
  const { fetch, calls } = fakeFetch(respond);
  const logged: unknown[] = [];
  const client = createWriteClient({ baseUrl: BASE, fetch, token, logError: (e) => logged.push(e) });
  return { client, calls, logged };
}

describe('createWriteClient requests', () => {
  it('PUTs the profile as JSON to the loopback backend with the credentials, refusing redirects', async () => {
    const { client, calls } = build(() => json(PROFILE));
    expect(await client.putProfile(ASTRONAUT, { astronaut_id: ASTRONAUT })).toEqual({ ok: true, value: PROFILE });
    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call?.url).toBe(`${BASE}/ui/crew/${ASTRONAUT}/profile`);
    expect(call?.init.method).toBe('PUT');
    expect(call?.init.redirect).toBe('error');
    expect(call?.init.signal).toBeInstanceOf(AbortSignal);
    expect(call?.init.headers).toEqual({
      accept: 'application/json',
      'content-type': 'application/json',
      authorization: 'Bearer tok',
      'x-astronaut-id': ASTRONAUT,
    });
    expect(JSON.parse(call?.init.body ?? '')).toEqual({ astronaut_id: ASTRONAUT });
  });

  it('PUTs the daily context of one day', async () => {
    const { client, calls } = build(() => json(CONTEXT));
    expect(await client.putDailyContext(ASTRONAUT, DAY, { phase: 'inflight' })).toEqual({ ok: true, value: CONTEXT });
    expect(calls[0]?.url).toBe(`${BASE}/ui/crew/${ASTRONAUT}/daily-context/${DAY}`);
    expect(calls[0]?.init.headers).not.toHaveProperty('if-none-match');
  });

  it('creates the daily context of one day only when none is stored, with If-None-Match: *', async () => {
    const { client, calls } = build(() => json(CONTEXT));
    expect(await client.createDailyContext(ASTRONAUT, DAY, { phase: 'inflight' })).toEqual({ ok: true, value: CONTEXT });
    expect(calls[0]?.url).toBe(`${BASE}/ui/crew/${ASTRONAUT}/daily-context/${DAY}`);
    expect(calls[0]?.init.method).toBe('PUT');
    expect(calls[0]?.init.headers).toEqual({
      accept: 'application/json',
      'content-type': 'application/json',
      'if-none-match': '*',
      authorization: 'Bearer tok',
      'x-astronaut-id': ASTRONAUT,
    });
  });

  it('sends no authorization header without a token and encodes the path', async () => {
    const { client, calls } = build(() => json({}, 201), null);
    const result = await client.send({ method: 'POST', path: '/ui/x', astronautId: ASTRONAUT, body: {}, schema: z.looseObject({}) });
    expect(result).toEqual({ ok: true, value: {} });
    expect(calls[0]?.init.method).toBe('POST');
    expect(calls[0]?.init.headers).not.toHaveProperty('authorization');
  });

  it('POSTs without a body when the request has none', async () => {
    const { client, calls } = build(() => json({}, 202));
    await client.send({ method: 'POST', path: '/ui/x', astronautId: ASTRONAUT, body: undefined, schema: z.looseObject({}) });
    expect(calls[0]?.init).not.toHaveProperty('body');
    expect(calls[0]?.init.headers).not.toHaveProperty('content-type');
  });

  it('refuses a non-loopback backend', () => {
    expect(() => createWriteClient({ baseUrl: 'http://example.com' })).toThrow(/loopback/);
  });
});

describe('createWriteClient status mapping', () => {
  it.each([
    [401, 'locked'],
    [403, 'locked'],
    [409, 'conflict'],
    [412, 'unavailable'],
    [500, 'unavailable'],
    [503, 'unavailable'],
    [404, 'unavailable'],
    [302, 'unavailable'],
  ])('%i is %s', async (status, reason) => {
    const { client } = build(() => problem(status));
    expect(await client.putProfile(ASTRONAUT, {})).toEqual({ ok: false, reason });
  });

  it('412 to a create is exists, and is not logged as a failure', async () => {
    const { client, logged } = build(() => problem(412));
    expect(await client.createDailyContext(ASTRONAUT, DAY, {})).toEqual({ ok: false, reason: 'exists' });
    expect(logged).toEqual([]);
  });

  it.each([
    [409, 'conflict'],
    [500, 'unavailable'],
  ])('a create answered %i is %s', async (status, reason) => {
    const { client } = build(() => problem(status));
    expect(await client.createDailyContext(ASTRONAUT, DAY, {})).toEqual({ ok: false, reason });
  });

  it('422 carries only the pointer and message of each well-formed field error', async () => {
    const { client } = build(() =>
      problem(422, {
        errors: [
          { pointer: '/anthropometry/body_mass_kg', message: 'too small', extra: 'dropped' },
          { pointer: 7, message: 'bad pointer' },
          'not an object',
          { pointer: '/date_of_birth', message: 'not a date' },
        ],
      }),
    );
    expect(await client.putProfile(ASTRONAUT, {})).toEqual({
      ok: false,
      reason: 'invalid',
      fields: [
        { pointer: '/anthropometry/body_mass_kg', message: 'too small' },
        { pointer: '/date_of_birth', message: 'not a date' },
      ],
    });
  });

  it.each([
    ['without errors', problem(422)],
    ['with errors that is not a list', problem(422, { errors: 'nope' })],
    ['with a body that is not JSON', new Response('<html>', { status: 422 })],
  ])('422 %s has no fields', async (_label, response) => {
    const { client } = build(() => response);
    expect(await client.putProfile(ASTRONAUT, {})).toEqual({ ok: false, reason: 'invalid', fields: [] });
  });

  it('a network failure is unavailable and its cause is logged', async () => {
    const cause = new TypeError('fetch failed');
    const { client, logged } = build(() => Promise.reject(cause));
    expect(await client.putProfile(ASTRONAUT, {})).toEqual({ ok: false, reason: 'unavailable' });
    expect(logged).toEqual([cause]);
  });

  it.each([
    ['not JSON', new Response('not json', { status: 200 })],
    ['another astronaut', json({ ...PROFILE, astronaut_id: 'AST-02' })],
    ['a wrong contract major', json({ ...PROFILE, schema_version: '2.0.0' })],
    ['no anthropometry', json({ ...PROFILE, anthropometry: undefined })],
  ])('a 2xx profile body that is %s is unavailable', async (_label, response) => {
    const { client, logged } = build(() => response);
    expect(await client.putProfile(ASTRONAUT, {})).toEqual({ ok: false, reason: 'unavailable' });
    expect(logged).toHaveLength(1);
  });

  it('a 2xx daily context of another day is unavailable', async () => {
    const { client } = build(() => json({ ...CONTEXT, date: '2026-11-12' }));
    expect(await client.putDailyContext(ASTRONAUT, DAY, {})).toEqual({ ok: false, reason: 'unavailable' });
  });
});
