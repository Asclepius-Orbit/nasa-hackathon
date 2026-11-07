import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { IPC_CHANNELS } from '../shared/ipc-channels';
import { authStateSchema, createAuthService } from './auth/auth-service';
import { createPinHasher } from './auth/pin';
import { registerIpc, type IpcMainLike, type IpcServices } from './ipc';
import { createAppUrlPolicy, createSenderGuard, type IpcEventLike } from './security/app-url';
import { createCrewStore, crewFileSchema } from './store/crew-store';
import { DEMO_CREW } from './store/demo-crew';
import { createEncryptedJsonFile } from './store/encrypted-file';
import { createSettingsStore } from './store/settings-store';
import { FakeClock, FakeSafeStorage, FlakyFs, makeTempDir } from './store/test-fakes';

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

const APP_URL = 'app://asclepius/index.html';
const trusted: IpcEventLike = { senderFrame: { url: `${APP_URL}#/login`, parent: null } };
const untrustedEvents: [string, IpcEventLike][] = [
  ['no frame', { senderFrame: null }],
  ['subframe', { senderFrame: { url: APP_URL, parent: { url: APP_URL, parent: null } } }],
  ['foreign page', { senderFrame: { url: 'https://example.com/', parent: null } }],
];

let dir: string;
let cleanup: () => Promise<void>;
let safeStorage: FakeSafeStorage;
let fs: FlakyFs;
let ipc: FakeIpcMain;
let services: IpcServices;
let logged: unknown[];

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
  services = { crew, auth, settings };
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
  ])('rejects invalid input on %s %j without echoing it', async (channel, args) => {
    await expect(call(channel, ...args)).rejects.toThrow(/^invalid input$/);
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
