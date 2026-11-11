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

  describe('session streams, reports due and contacts', () => {
    const SESSION = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';
    const RAW_SESSION = {
      session: {
        session_id: SESSION,
        astronaut_id: ASTRONAUT,
        streams: [
          { stream_id: 'emg_lat_left', stream_kind: 'emg', placement: { muscle: 'latissimus_dorsi', side: 'left' }, sample_rate_hz: 1000 },
          { stream_id: 'imu_l3', stream_kind: 'imu', placement: { spine_level: 'l3' }, sample_rate_hz: 100 },
        ],
      },
      state: 'running',
      error: null,
      feature_set: null,
    };
    const DUE = {
      date: DAY,
      daily_due: true,
      weekly_odi_due: false,
      red_flag_available: true,
      last_daily_at: null,
      last_weekly_odi_at: '2026-11-08T09:00:00+00:00',
    };
    const CONTACTS = {
      schema_version: '1.0.0',
      astronaut_id: ASTRONAUT,
      contacts: [
        { schema_version: '1.0.0', contact_id: 'c0a7c7e0-0000-4000-8000-000000000001', astronaut_id: ASTRONAUT, at: '2026-11-11T10:00:00Z', contact: 'crew_medical_officer', session_id: SESSION },
        { schema_version: '1.0.0', contact_id: 'c0a7c7e0-0000-4000-8000-000000000002', astronaut_id: ASTRONAUT, at: '2026-11-11T11:00:00Z', contact: 'crew_medical_officer' },
      ],
    };

    it("GETs a session's declared streams (id and kind)", async () => {
      const { client, calls } = build(() => json(RAW_SESSION));
      expect(await client.sessionStreams(ASTRONAUT, SESSION)).toEqual([
        { streamId: 'emg_lat_left', kind: 'emg' },
        { streamId: 'imu_l3', kind: 'imu' },
      ]);
      expect(calls[0]?.url).toBe(`${BASE}/ui/sessions/${SESSION}`);
      expect(calls[0]?.init.headers).toMatchObject({ 'x-astronaut-id': ASTRONAUT });
    });

    it('answers null for an unknown session', async () => {
      const { client } = build(() => json({}, 404));
      expect(await client.sessionStreams(ASTRONAUT, SESSION)).toBeNull();
    });

    it("maps which self-reports are due", async () => {
      const { client, calls } = build(() => json(DUE));
      expect(await client.reportsDue(ASTRONAUT)).toEqual({
        dailyDue: true,
        weeklyOdiDue: false,
        lastDailyAt: null,
        lastWeeklyOdiAt: '2026-11-08T09:00:00+00:00',
      });
      expect(calls[0]?.url).toBe(`${BASE}/ui/crew/${ASTRONAUT}/self-reports/due`);
    });

    it('maps the medical contacts, a missing session as null', async () => {
      const { client, calls } = build(() => json(CONTACTS));
      expect(await client.contacts(ASTRONAUT)).toEqual([
        { contactId: 'c0a7c7e0-0000-4000-8000-000000000001', at: '2026-11-11T10:00:00Z', sessionId: SESSION },
        { contactId: 'c0a7c7e0-0000-4000-8000-000000000002', at: '2026-11-11T11:00:00Z', sessionId: null },
      ]);
      expect(calls[0]?.url).toBe(`${BASE}/ui/crew/${ASTRONAUT}/contacts`);
    });

    it('answers no contacts for 404 and rejects a due that is not stored as unavailable', async () => {
      const { client } = build(() => json({}, 404));
      expect(await client.contacts(ASTRONAUT)).toEqual([]);
      expect(await codeOf(client.reportsDue(ASTRONAUT))).toBe('unavailable');
    });

    it.each([
      ['session of another astronaut', (c: RecordsClient) => c.sessionStreams(ASTRONAUT, SESSION), { session: { ...RAW_SESSION.session, astronaut_id: 'AST-02' } }],
      ['session without streams', (c: RecordsClient) => c.sessionStreams(ASTRONAUT, SESSION), { session: { astronaut_id: ASTRONAUT } }],
      ['due without flags', (c: RecordsClient) => c.reportsDue(ASTRONAUT), { date: DAY }],
      ['contacts of another astronaut', (c: RecordsClient) => c.contacts(ASTRONAUT), { ...CONTACTS, astronaut_id: 'AST-02' }],
    ])('rejects a %s as unavailable', async (_label, read, body) => {
      const { client } = build(() => json(body));
      expect(await codeOf(read(client))).toBe('unavailable');
    });
  });

  it('refuses a non-loopback backend', () => {
    expect(() => createRecordsClient({ baseUrl: 'http://example.com' })).toThrow(/loopback/);
  });
});
