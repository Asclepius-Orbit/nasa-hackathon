import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { IPC_CHANNELS } from '../shared/ipc-channels';
import { authStateSchema, createAuthService } from './auth/auth-service';
import { createPinHasher } from './auth/pin';
import { registerIpc, type IpcMainLike, type IpcServices } from './ipc';
import { createAppUrlPolicy, createSenderGuard, type IpcEventLike, type WebContentsLike } from './security/app-url';
import { createCrewStore, crewFileSchema } from './store/crew-store';
import { DEMO_CREW } from './store/demo-crew';
import { createEncryptedJsonFile } from './store/encrypted-file';
import { createSettingsStore } from './store/settings-store';
import { FakeClock, FakeSafeStorage, FlakyFs, makeTempDir } from './store/test-fakes';
import type { RepsService } from './views/reps-service';
import type { ViewsClient } from './views/views-client';
import { ViewsError } from './views/views-error';
import { createViewsService } from './views/views-service';

type Listener = (event: IpcEventLike, ...args: unknown[]) => unknown;

/** Stands in for Electron's ipcMain: records handlers and lets the test invoke them as a renderer would. */
class FakeIpcMain implements IpcMainLike {
  readonly handlers = new Map<string, Listener>();
  handle(channel: string, listener: Listener): void {
    if (this.handlers.has(channel)) throw new Error(`duplicate handler ${channel}`);
    this.handlers.set(channel, listener);
  }
  async invoke(channel: string, event: IpcEventLike, ...args: unknown[]): Promise<unknown> {
    const listener = this.handlers.get(channel);
    if (!listener) throw new Error(`no handler ${channel}`);
    return await listener(event, ...args);
  }
}

const SESSION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const APP_URL = 'app://asclepius/index.html';
const SUBSCRIPTION = '0b9c3d4e-1f2a-4b5c-8d6e-7f8091a2b3c4';
const sender: WebContentsLike = {
  send: () => undefined,
  isDestroyed: () => false,
  once: () => undefined,
  removeListener: () => undefined,
};
const trusted: IpcEventLike = { sender, senderFrame: { url: `${APP_URL}#/login`, parent: null } };
const untrustedEvents: [string, IpcEventLike][] = [
  ['no frame', { sender, senderFrame: null }],
  ['subframe', { sender, senderFrame: { url: APP_URL, parent: { url: APP_URL, parent: null } } }],
  ['foreign page', { sender, senderFrame: { url: 'https://example.com/', parent: null } }],
];

let dir: string;
let cleanup: () => Promise<void>;
let safeStorage: FakeSafeStorage;
let fs: FlakyFs;
let ipc: FakeIpcMain;
let services: IpcServices;
let logged: unknown[];
/** Fake of the backend HTTP client: remembers the arguments and answers (or fails) as told. */
let backend: { calls: unknown[][]; failWith: Error | null };
/** Fake of the repetition service: remembers what the handlers ask of it. */
let repsCalls: unknown[][];
let repsFailWith: Error | null;

function fakeRepsService(): RepsService {
  repsCalls = [];
  repsFailWith = null;
  return {
    subscribe: (from, sessionId) => {
      repsCalls.push(['subscribe', from, sessionId]);
      return repsFailWith === null ? Promise.resolve(SUBSCRIPTION) : Promise.reject(repsFailWith);
    },
    unsubscribe: (from, id) => {
      repsCalls.push(['unsubscribe', from, id]);
    },
    closeAll: () => {
      repsCalls.push(['closeAll']);
    },
  };
}

function fakeViewsClient(): ViewsClient {
  backend = { calls: [], failWith: null };
  const view = (name: string, ...args: unknown[]): Promise<never> => {
    backend.calls.push([name, ...args]);
    if (backend.failWith !== null) return Promise.reject(backend.failWith);
    return Promise.resolve({ schema_version: '1.0.0', view: name } as never);
  };
  return {
    strip: (id) => view('strip', id),
    today: (id) => view('today', id),
    history: (id, range) => view('history', id, range),
    baseline: (id) => view('baseline', id),
    firstDay: (id) => view('firstDay', id),
    session: (astronautId, id) => view('session', astronautId, id),
    houston: (id) => view('houston', id),
    outbox: (id) => view('outbox', id),
  };
}

beforeEach(async () => {
  ({ dir, cleanup } = await makeTempDir());
  safeStorage = new FakeSafeStorage();
  fs = new FlakyFs();
  const hasher = createPinHasher({ N: 2 ** 10, r: 8, p: 1 });
  const crew = createCrewStore({
    file: createEncryptedJsonFile({ path: join(dir, 'crew.bin'), schema: crewFileSchema, safeStorage, fs }),
    hasher,
    seed: DEMO_CREW,
  });
  const auth = createAuthService({
    crew,
    hasher,
    stateFile: createEncryptedJsonFile({ path: join(dir, 'auth-state.bin'), schema: authStateSchema, safeStorage, fs }),
    now: new FakeClock().now,
  });
  const settings = createSettingsStore({ path: join(dir, 'settings.json'), fs });
  const views = createViewsService({ auth, crew, client: fakeViewsClient() });
  services = { crew, auth, settings, views, reps: fakeRepsService() };
  ipc = new FakeIpcMain();
  logged = [];
  const policy = createAppUrlPolicy({});
  registerIpc(ipc, services, createSenderGuard(policy), (error) => logged.push(error));
});
afterEach(async () => {
  await cleanup();
});

const call = (channel: string, ...args: unknown[]) => ipc.invoke(channel, trusted, ...args);

describe('registerIpc', () => {
  it('registers exactly one handler per channel', () => {
    expect([...ipc.handlers.keys()].sort()).toEqual(Object.values(IPC_CHANNELS).sort());
  });

  describe.each(untrustedEvents)('rejects an untrusted sender (%s)', (_label, event) => {
    it.each(Object.values(IPC_CHANNELS))('on %s', async (channel) => {
      await expect(ipc.invoke(channel, event)).rejects.toThrow(/^forbidden$/);
    });

    it('before touching any service', async () => {
      await expect(ipc.invoke(IPC_CHANNELS.settingsUpdate, event, { lengthUnit: 'in' })).rejects.toThrow('forbidden');
      expect((await services.settings.get()).lengthUnit).toBe('mm');
    });
  });

  it.each([
    [IPC_CHANNELS.crewList, ['extra']],
    [IPC_CHANNELS.crewNextId, [{}]],
    [IPC_CHANNELS.authLock, [undefined]],
    [IPC_CHANNELS.authCurrent, [1]],
    [IPC_CHANNELS.settingsGet, [null]],
    [IPC_CHANNELS.crewCreate, []],
    [IPC_CHANNELS.crewCreate, [{ name: 'x'.repeat(41), pin: '1234' }]],
    [IPC_CHANNELS.crewCreate, [{ name: 'Ana', pin: '1234', isAdmin: true }]],
    [IPC_CHANNELS.crewCreate, [{ name: 'Ana', pin: '1234' }, 'extra']],
    [IPC_CHANNELS.authVerifyPin, [{ crewId: 'AST-1', pin: '1234' }]],
    [IPC_CHANNELS.authVerifyPin, [{ crewId: 'AST-01', pin: '12' }]],
    [IPC_CHANNELS.settingsUpdate, [{ lengthUnit: 'cm' }]],
    [IPC_CHANNELS.settingsUpdate, [{ theme: 'dark' }]],
    [IPC_CHANNELS.settingsUpdate, []],
    [IPC_CHANNELS.viewsStrip, ['AST-02']],
    [IPC_CHANNELS.viewsHouston, ['AST-02']],
    [IPC_CHANNELS.viewsOutbox, [{ astronautId: 'AST-02' }]],
    [IPC_CHANNELS.viewsToday, [{}]],
    [IPC_CHANNELS.viewsBaseline, ['AST-02']],
    [IPC_CHANNELS.viewsFirstDay, [{ astronautId: 'AST-02' }]],
    [IPC_CHANNELS.viewsHistory, []],
    [IPC_CHANNELS.viewsHistory, ['2026-11-01']],
    [IPC_CHANNELS.viewsHistory, [{ from: '2026-13-01' }]],
    [IPC_CHANNELS.viewsHistory, [{ from: '2026-11-08', to: '2026-11-01' }]],
    [IPC_CHANNELS.viewsHistory, [{ from: '2026-11-01', astronautId: 'AST-02' }]],
    [IPC_CHANNELS.viewsHistory, [{}, 'extra']],
    [IPC_CHANNELS.viewsSession, []],
    [IPC_CHANNELS.viewsSession, ['not-a-uuid']],
    [IPC_CHANNELS.viewsSession, [{ sessionId: '../../etc' }]],
    [IPC_CHANNELS.viewsSession, [{ sessionId: SESSION, astronautId: 'AST-02' }]],
    [IPC_CHANNELS.repsSubscribe, []],
    [IPC_CHANNELS.repsSubscribe, [SESSION]],
    [IPC_CHANNELS.repsSubscribe, [{ sessionId: '../../etc' }]],
    [IPC_CHANNELS.repsSubscribe, [{ sessionId: SESSION.toUpperCase() }]],
    [IPC_CHANNELS.repsSubscribe, [{ sessionId: SESSION, astronautId: 'AST-02' }]],
    [IPC_CHANNELS.repsSubscribe, [{ sessionId: SESSION }, 'extra']],
    [IPC_CHANNELS.repsUnsubscribe, []],
    [IPC_CHANNELS.repsUnsubscribe, [{ subscriptionId: 'abc' }]],
    [IPC_CHANNELS.repsUnsubscribe, [{ subscriptionId: SUBSCRIPTION, x: 1 }]],
  ])('rejects invalid input on %s %j without echoing it', async (channel, args) => {
    await expect(call(channel, ...args)).rejects.toThrow(/^invalid input$/);
    expect(repsCalls).toEqual([]);
  });
});

describe('crew channels', () => {
  it('lists DTOs and the next id', async () => {
    expect(await call(IPC_CHANNELS.crewList)).toEqual([
      { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-01' },
      { id: 'AST-02', name: 'Marc Dubois', harnessId: 'wb-02' },
    ]);
    expect(await call(IPC_CHANNELS.crewNextId)).toBe('AST-03');
  });

  it('creates a member and signs them in', async () => {
    expect(await call(IPC_CHANNELS.crewCreate, { name: 'Ana García', pin: '4321' })).toEqual({
      ok: true,
      member: { id: 'AST-03', name: 'Ana García', harnessId: null },
    });
    expect(await call(IPC_CHANNELS.authCurrent)).toEqual({ crewId: 'AST-03', locked: false });
  });

  it('does not sign in when create fails', async () => {
    expect(await call(IPC_CHANNELS.crewCreate, { name: 'A', pin: '4321' })).toEqual({
      ok: false,
      reason: 'name_too_short',
    });
    expect(await call(IPC_CHANNELS.authCurrent)).toBeNull();
  });

  it('refuses to create a member behind the lock screen (that would bypass it)', async () => {
    await call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '1234' });
    await call(IPC_CHANNELS.authLock);
    expect(await call(IPC_CHANNELS.crewCreate, { name: 'Ana', pin: '4321' })).toEqual({
      ok: false,
      reason: 'unavailable',
    });
    expect(await call(IPC_CHANNELS.authCurrent)).toEqual({ crewId: 'AST-01', locked: true });
    expect(await call(IPC_CHANNELS.crewNextId)).toBe('AST-03');
  });

  it('rejects list and nextId with "unavailable" when the crew store is unavailable', async () => {
    safeStorage.available = false;
    await expect(call(IPC_CHANNELS.crewList)).rejects.toThrow(/^unavailable$/);
    await expect(call(IPC_CHANNELS.crewNextId)).rejects.toThrow(/^unavailable$/);
  });
});

describe('auth channels', () => {
  it('verifies, locks, and reports the session', async () => {
    expect(await call(IPC_CHANNELS.authCurrent)).toBeNull();
    expect(await call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '0000' })).toEqual({
      ok: false,
      reason: 'wrong_pin',
      attemptsLeft: 2,
    });
    expect(await call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '1234' })).toEqual({
      ok: true,
      crewId: 'AST-01',
    });
    expect(await call(IPC_CHANNELS.authLock)).toBeUndefined();
    expect(await call(IPC_CHANNELS.authCurrent)).toEqual({ crewId: 'AST-01', locked: true });
  });
});

describe('settings channels', () => {
  it('gets and updates', async () => {
    expect(await call(IPC_CHANNELS.settingsGet)).toMatchObject({ lengthUnit: 'mm' });
    expect(await call(IPC_CHANNELS.settingsUpdate, { lengthUnit: 'in' })).toMatchObject({ lengthUnit: 'in' });
    expect(await call(IPC_CHANNELS.settingsGet)).toMatchObject({ lengthUnit: 'in' });
  });

  it('turns an unexpected service failure into a generic error and logs the original', async () => {
    fs.failRename = true;
    await expect(call(IPC_CHANNELS.settingsUpdate, { lengthUnit: 'in' })).rejects.toThrow(/^unavailable$/);
    expect(logged).toHaveLength(1);
  });

  it('never forwards the original error message (it may hold paths or data)', async () => {
    const leaky = new FakeIpcMain();
    const bug = new Error('EACCES: C:\\Users\\elena\\AppData\\Roaming\\spine-ui\\settings.json');
    const broken = { get: () => Promise.reject(bug), update: () => Promise.reject(bug) };
    registerIpc(leaky, { ...services, settings: broken }, () => true, (error) => logged.push(error));
    await expect(leaky.invoke(IPC_CHANNELS.settingsGet, trusted)).rejects.toThrow(/^unavailable$/);
    expect(logged).toEqual([bug]);
  });
});

describe('views channels', () => {
  const unlock = () => call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '1234' });

  it('forwards each call to the backend for the unlocked astronaut only', async () => {
    await unlock();
    expect(await call(IPC_CHANNELS.viewsStrip)).toEqual({ schema_version: '1.0.0', view: 'strip' });
    await call(IPC_CHANNELS.viewsToday);
    await call(IPC_CHANNELS.viewsHistory, {});
    await call(IPC_CHANNELS.viewsHistory, { from: '2026-11-01', to: '2026-11-07' });
    await call(IPC_CHANNELS.viewsBaseline);
    await call(IPC_CHANNELS.viewsSession, { sessionId: SESSION });
    await call(IPC_CHANNELS.viewsHouston);
    await call(IPC_CHANNELS.viewsOutbox);
    expect(backend.calls).toEqual([
      ['strip', 'AST-01'],
      ['today', 'AST-01'],
      ['history', 'AST-01', {}],
      ['history', 'AST-01', { from: '2026-11-01', to: '2026-11-07' }],
      ['baseline', 'AST-01'],
      ['session', 'AST-01', SESSION],
      ['houston', 'AST-01'],
      ['outbox', 'AST-01'],
    ]);
  });

  it('adds the crew first name to first-day', async () => {
    await unlock();
    expect(await call(IPC_CHANNELS.viewsFirstDay)).toEqual({
      schema_version: '1.0.0',
      view: 'firstDay',
      crewFirstName: 'Elena',
    });
  });

  it.each([IPC_CHANNELS.viewsStrip, IPC_CHANNELS.viewsToday, IPC_CHANNELS.viewsBaseline, IPC_CHANNELS.viewsFirstDay, IPC_CHANNELS.viewsHouston, IPC_CHANNELS.viewsOutbox])(
    'rejects %s with "locked" before login and behind the lock screen',
    async (channel) => {
      await expect(call(channel)).rejects.toThrow(/^locked$/);
      await unlock();
      await call(IPC_CHANNELS.authLock);
      await expect(call(channel)).rejects.toThrow(/^locked$/);
      expect(backend.calls).toEqual([]);
    },
  );

  it('rejects history and session with "locked" too', async () => {
    await expect(call(IPC_CHANNELS.viewsHistory, {})).rejects.toThrow(/^locked$/);
    await expect(call(IPC_CHANNELS.viewsSession, { sessionId: SESSION })).rejects.toThrow(/^locked$/);
    expect(backend.calls).toEqual([]);
  });

  it('passes "not_found" through without logging it', async () => {
    await unlock();
    backend.failWith = new ViewsError('not_found');
    await expect(call(IPC_CHANNELS.viewsSession, { sessionId: SESSION })).rejects.toThrow(/^not_found$/);
    expect(logged).toEqual([]);
  });

  it('turns backend failures into "unavailable" and logs the cause', async () => {
    await unlock();
    const cause = new TypeError('fetch failed');
    backend.failWith = new ViewsError('unavailable', { cause });
    await expect(call(IPC_CHANNELS.viewsToday)).rejects.toThrow(/^unavailable$/);
    expect(logged).toEqual([cause]);
  });

  it('does not log an unavailable view that has no underlying error', async () => {
    await unlock();
    backend.failWith = new ViewsError('unavailable');
    await expect(call(IPC_CHANNELS.viewsToday)).rejects.toThrow(/^unavailable$/);
    expect(logged).toEqual([]);
  });

  it('keeps hiding unexpected errors behind "unavailable"', async () => {
    await unlock();
    backend.failWith = new Error('boom C:\\secret');
    await expect(call(IPC_CHANNELS.viewsStrip)).rejects.toThrow(/^unavailable$/);
    expect(logged).toHaveLength(1);
  });
});

describe('reps channels', () => {
  const forbiddenEvent: IpcEventLike = { sender, senderFrame: null };

  it('subscribes the calling window to the requested session and returns the subscription id', async () => {
    expect(await call(IPC_CHANNELS.repsSubscribe, { sessionId: SESSION })).toBe(SUBSCRIPTION);
    expect(repsCalls).toEqual([['subscribe', sender, SESSION]]);
  });

  it('unsubscribes through the calling window', async () => {
    expect(await call(IPC_CHANNELS.repsUnsubscribe, { subscriptionId: SUBSCRIPTION })).toBeUndefined();
    expect(repsCalls).toEqual([['unsubscribe', sender, SUBSCRIPTION]]);
  });

  it('does not reach the service from an untrusted sender', async () => {
    await expect(ipc.invoke(IPC_CHANNELS.repsSubscribe, forbiddenEvent, { sessionId: SESSION })).rejects.toThrow('forbidden');
    expect(repsCalls).toEqual([]);
  });

  it.each(['locked', 'not_found'] as const)('passes "%s" through without logging it', async (code) => {
    repsFailWith = new ViewsError(code);
    await expect(call(IPC_CHANNELS.repsSubscribe, { sessionId: SESSION })).rejects.toThrow(new RegExp(`^${code}$`, 'u'));
    expect(logged).toEqual([]);
  });

  it('hides any other failure behind "unavailable" and logs it', async () => {
    const bug = new Error('boom C:\\secret');
    repsFailWith = bug;
    await expect(call(IPC_CHANNELS.repsSubscribe, { sessionId: SESSION })).rejects.toThrow(/^unavailable$/);
    expect(logged).toEqual([bug]);
  });

  it('stops every live stream when the app is locked', async () => {
    await call(IPC_CHANNELS.authLock);
    expect(repsCalls).toEqual([['closeAll']]);
  });
});
