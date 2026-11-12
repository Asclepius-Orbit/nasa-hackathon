import { describe, expect, it } from 'vitest';

import { createRecordsClient, type RecordsClient } from './records-client';
import type { ViewFetch } from './views-client';
import { ViewsError } from './views-error';

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
const CONTEXT = { schema_version: '1.0.0', astronaut_id: ASTRONAUT, date: DAY, phase: 'inflight', gravity_environment: 'microgravity' };
const DEVICE = {
  schema_version: '1.0.0',
  astronaut_id: ASTRONAUT,
  device_id: 'wb-01',
  timestamp: '2026-11-11T10:00:00+00:00',
  status: 'ok',
  source: 'simulator',
};

function build(respond: () => Promise<Response> | Response) {
  const calls: { url: string; init: Parameters<ViewFetch>[1] }[] = [];
  const fetch: ViewFetch = (url, init) => {
    calls.push({ url, init });
    return Promise.resolve().then(respond);
  };
  return { client: createRecordsClient({ baseUrl: BASE, fetch, token: 'tok' }), calls };
}

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status });

async function codeOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ViewsError) return error.code;
    throw error;
  }
  throw new Error('expected a ViewsError');
}

const reads: [string, (c: RecordsClient) => Promise<unknown>, string, unknown][] = [
  ['profile', (c) => c.profile(ASTRONAUT), `/ui/crew/${ASTRONAUT}/profile`, PROFILE],
  ['daily context', (c) => c.dailyContext(ASTRONAUT, DAY), `/ui/crew/${ASTRONAUT}/daily-context/${DAY}`, CONTEXT],
  ['devices', (c) => c.devices(ASTRONAUT), `/ui/crew/${ASTRONAUT}/devices`, [DEVICE]],
];

describe('createRecordsClient', () => {
  it('returns the version of a profile and refuses a response without one', async () => {
    const withTag = build(() => new Response(JSON.stringify(PROFILE), { headers: { etag: '"v1"' } }));
    expect(await withTag.client.profileVersioned(ASTRONAUT)).toEqual({ value: PROFILE, etag: '"v1"' });
    expect(await codeOf(build(() => json(PROFILE)).client.profileVersioned(ASTRONAUT))).toBe('unavailable');
  });

  it.each(reads)('GETs the %s with the credentials and returns the body', async (_label, read, path, body) => {
    const { client, calls } = build(() => json(body));
    expect(await read(client)).toEqual(body);
    expect(calls[0]?.url).toBe(`${BASE}${path}`);
    expect(calls[0]?.init).toMatchObject({
      method: 'GET',
      redirect: 'error',
      headers: { accept: 'application/json', authorization: 'Bearer tok', 'x-astronaut-id': ASTRONAUT },
    });
  });

  it('answers null for a profile or daily context that is not stored, and no devices for 404', async () => {
    const { client } = build(() => json({ status: 404 }, 404));
    expect(await client.profile(ASTRONAUT)).toBeNull();
    expect(await client.dailyContext(ASTRONAUT, DAY)).toBeNull();
    expect(await client.devices(ASTRONAUT)).toEqual([]);
  });

  it.each([
    [401, 'locked'],
    [403, 'locked'],
    [500, 'unavailable'],
  ])('rejects %i with %s', async (status, code) => {
    for (const [, read] of reads) {
      const { client } = build(() => json({}, status));
      expect(await codeOf(read(client))).toBe(code);
    }
  });

  it('rejects a network failure as unavailable with the cause', async () => {
    const cause = new TypeError('fetch failed');
    const { client } = build(() => Promise.reject(cause));
    const error = await client.profile(ASTRONAUT).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ViewsError);
    expect((error as ViewsError).cause).toBe(cause);
  });

  it.each([
    ['profile of another astronaut', (c: RecordsClient) => c.profile(ASTRONAUT), json({ ...PROFILE, astronaut_id: 'AST-02' })],
    ['daily context of another day', (c: RecordsClient) => c.dailyContext(ASTRONAUT, DAY), json({ ...CONTEXT, date: '2026-11-10' })],
    ['devices that are not a list', (c: RecordsClient) => c.devices(ASTRONAUT), json(DEVICE)],
    ['a device without id', (c: RecordsClient) => c.devices(ASTRONAUT), json([{ ...DEVICE, device_id: 3 }])],
    ['a device of another astronaut', (c: RecordsClient) => c.devices(ASTRONAUT), json([{ ...DEVICE, astronaut_id: 'AST-02' }])],
    ['a body that is not JSON', (c: RecordsClient) => c.profile(ASTRONAUT), new Response('nope')],
  ])('rejects a %s as unavailable', async (_label, read, response) => {
    const { client } = build(() => response);
    expect(await codeOf(read(client))).toBe('unavailable');
  });

  it('refuses a non-loopback backend', () => {
    expect(() => createRecordsClient({ baseUrl: 'http://example.com' })).toThrow(/loopback/);
  });
});
