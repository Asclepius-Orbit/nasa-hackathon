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
import { createDailyContextService } from './profile/daily-context-service';
import { createDevicesService } from './profile/devices-service';
import { createHarnessService } from './profile/harness-service';
import { createProfileService } from './profile/profile-service';
import { FakeBackend } from './profile/test-fakes';
import { createContactsService } from './reports/contacts-service';
import { createReportsService } from './reports/reports-service';
import { createUrgentService } from './reports/urgent-service';
import { createSessionsService } from './sessions/sessions-service';
import type { ChatService } from './chat/chat-service';
import { createHoustonReportService } from './views/houston-report-service';
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

const PROFILE_FIELDS = {
  date_of_birth: '1985-04-02',
  sex_at_birth: 'female',
  launch_date: '2026-11-01',
  landing_date: null,
  height_standing_cm: 171,
  height_sitting_cm: null,
  body_mass_kg: 61.5,
};
const DAILY_FIELDS = {
  pain_nrs: 3,
  stiffness_nrs: 2,
  pain_locations: [],
  sleep_hours: 7,
  sleep_quality_nrs: null,
  red_flags: [],
  exercise: null,
};
const CONTEXT_FIELDS = { phase: 'inflight', gravity_environment: 'microgravity', mission_day: 5 };
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
/** What the fake save dialog was offered and what was written. */
let saved: { dialogs: string[]; written: [string, number][] };
/** Fake of the backend HTTP client: remembers the arguments and answers (or fails) as told. */
let backend: { calls: unknown[][]; failWith: Error | null };
/** Fake of the repetition service: remembers what the handlers ask of it. */
let repsCalls: unknown[][];
let repsFailWith: Error | null;
/** Fake backend of the profile, daily context and devices; and the astronauts `ensureToday` ran for. */
let records: FakeBackend;
let ensured: string[];

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

/** Fake of the chat service: remembers what the handlers ask of it. */
let chatCalls: unknown[][];
let chatFailWith: Error | null;
const CHAT_CONVERSATION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';

function fakeChatService(): ChatService {
  chatCalls = [];
  chatFailWith = null;
  return {
    ask: (from, input) => {
      chatCalls.push(['ask', from, input]);
      if (chatFailWith !== null) throw chatFailWith;
      return SUBSCRIPTION;
    },
    cancel: (from, id) => {
      chatCalls.push(['cancel', from, id]);
    },
    forget: (conversationId) => {
      chatCalls.push(['forget', conversationId]);
      return Promise.resolve();
    },
    stopAll: () => {
      chatCalls.push(['stopAll']);
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
    cordProtocols: (id) => view('cordProtocols', id),
    cordHistory: (id) => view('cordHistory', id),
    session: (astronautId, id) => view('session', astronautId, id),
    houston: (id) => view('houston', id),
    outbox: (id) => view('outbox', id),
    houstonReport: (id) => {
      backend.calls.push(['houstonReport', id]);
      return Promise.resolve({ fileName: 'houston-report.pdf', bytes: new Uint8Array([1]) });
    },
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
  records = new FakeBackend();
  ensured = [];
  saved = { dialogs: [], written: [] };
  const clock = new FakeClock();
  const dailyContextService = createDailyContextService({ auth, records: records.records, writer: records.writer, now: clock.now });
  const dailyContext = {
    ...dailyContextService,
    ensureToday: (id: string) => {
      ensured.push(id);
      return dailyContextService.ensureToday(id);
    },
  };
  services = {
    crew,
    auth,
    settings,
    views,
    reps: fakeRepsService(),
    chat: fakeChatService(),
    profile: createProfileService({ auth, records: records.records, writer: records.writer, dailyContext }),
    dailyContext,
    devices: createDevicesService({ auth, records: records.records }),
    harness: createHarnessService({ auth, crew }),
    sessions: createSessionsService({ auth, crew, records: records.records, writer: records.writer }),
    reports: createReportsService({ auth, records: records.records, writer: records.writer, dailyContext }),
    urgent: createUrgentService({ auth, crew, writer: records.writer, now: clock.now }),
    contacts: createContactsService({ auth, records: records.records, writer: records.writer }),
    houstonReport: createHoustonReportService({
      views,
      showSaveDialog: (name) => {
        saved.dialogs.push(name);
        return Promise.resolve({ canceled: false, filePath: join('chosen', name) });
      },
      writeFile: (path, bytes) => {
        saved.written.push([path, bytes.length]);
        return Promise.resolve();
      },
      openPath: () => Promise.resolve(''),
    }),
  };
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
    [IPC_CHANNELS.authSignOut, [undefined]],
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
    [IPC_CHANNELS.houstonReportGenerate, ['AST-02']],
    [IPC_CHANNELS.houstonReportGenerate, [{ path: '/tmp/x.pdf' }]],
    [IPC_CHANNELS.viewsBaseline, ['AST-02']],
    [IPC_CHANNELS.viewsFirstDay, [{ astronautId: 'AST-02' }]],
    [IPC_CHANNELS.viewsCordProtocols, [{ astronautId: 'AST-02' }]],
    [IPC_CHANNELS.viewsCordHistory, [{ astronautId: 'AST-02' }]],
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
    [IPC_CHANNELS.chatAsk, []],
    [IPC_CHANNELS.chatAsk, [{ conversationId: CHAT_CONVERSATION, question: '' }]],
    [IPC_CHANNELS.chatAsk, [{ conversationId: CHAT_CONVERSATION, question: '   ' }]],
    [IPC_CHANNELS.chatAsk, [{ conversationId: CHAT_CONVERSATION, question: 'x'.repeat(2001) }]],
    [IPC_CHANNELS.chatAsk, [{ conversationId: CHAT_CONVERSATION.toUpperCase(), question: 'q' }]],
    [IPC_CHANNELS.chatAsk, [{ conversationId: CHAT_CONVERSATION, question: 'q', astronautId: 'AST-02' }]],
    [IPC_CHANNELS.chatAsk, [{ conversationId: CHAT_CONVERSATION, question: 'q' }, 'extra']],
    [IPC_CHANNELS.chatCancel, [{ subscriptionId: 'abc' }]],
    [IPC_CHANNELS.chatCancel, []],
    [IPC_CHANNELS.chatForget, [{ conversationId: '../x' }]],
    [IPC_CHANNELS.chatForget, [CHAT_CONVERSATION]],
    [IPC_CHANNELS.crewSetHarness, []],
    [IPC_CHANNELS.crewSetHarness, [{ harnessId: 'WB 01' }]],
    [IPC_CHANNELS.crewSetHarness, [{ harnessId: 'wb-01', crewId: 'AST-02' }]],
    [IPC_CHANNELS.profileGet, ['AST-02']],
    [IPC_CHANNELS.profileUpdate, [{ ...PROFILE_FIELDS, date_of_birth: '1985-02-30' }]],
    [IPC_CHANNELS.profileUpdate, [{ ...PROFILE_FIELDS, body_mass_kg: Number.POSITIVE_INFINITY }]],
    [IPC_CHANNELS.profileUpdate, [{ ...PROFILE_FIELDS, astronaut_id: 'AST-02' }]],
    [IPC_CHANNELS.profileUpdate, [{ ...PROFILE_FIELDS, name: 'Elena' }]],
    [IPC_CHANNELS.dailyContextToday, [{ day: '2026-11-05' }]],
    [IPC_CHANNELS.dailyContextUpdateToday, [{ ...CONTEXT_FIELDS, mission_day: 1.5 }]],
    [IPC_CHANNELS.dailyContextUpdateToday, [{ ...CONTEXT_FIELDS, phase: 'orbit' }]],
    [IPC_CHANNELS.dailyContextUpdateToday, [{ ...CONTEXT_FIELDS, date: '2026-11-01' }]],
    [IPC_CHANNELS.devicesList, ['AST-02']],
    [IPC_CHANNELS.sessionsStart, []],
    [IPC_CHANNELS.sessionsStart, [{ protocolId: 'deadlift_v1' }]],
    [IPC_CHANNELS.sessionsStart, [{ protocolId: 'spine_length_rest_v1', deviceId: 'wb-09' }]],
    [IPC_CHANNELS.sessionsStop, [{ sessionId: SESSION, reason: 'bored' }]],
    [IPC_CHANNELS.sessionsStop, [{ sessionId: SESSION, reason: 'completed', repIndex: 3 }]],
    [IPC_CHANNELS.sessionsStop, [{ sessionId: SESSION, reason: 'aborted_pain', repIndex: 0 }]],
    [IPC_CHANNELS.sessionsStop, [{ sessionId: '../../etc', reason: 'completed' }]],
    [IPC_CHANNELS.sessionsMarkArtifact, [{ sessionId: SESSION }]],
    [IPC_CHANNELS.sessionsMarkArtifact, [{ sessionId: SESSION, repIndex: 1.5 }]],
    [IPC_CHANNELS.sessionsCalibrateMvc, [{ sessionId: SESSION.toUpperCase() }]],
    [IPC_CHANNELS.sessionsReprocess, [{ sessionId: SESSION, astronautId: 'AST-02' }]],
    [IPC_CHANNELS.reportsSaveDaily, [{ ...DAILY_FIELDS, astronaut_id: 'AST-02' }]],
    [IPC_CHANNELS.reportsSaveDaily, [{ ...DAILY_FIELDS, red_flags: ['Not A Code'] }]],
    [IPC_CHANNELS.reportsSaveDaily, [{ ...DAILY_FIELDS, exercise: { modality: 'yoga', duration_min: 10 } }]],
    [IPC_CHANNELS.reportsSaveOswestry, [{ items: [] }]],
    [IPC_CHANNELS.reportsSaveOswestry, [{ items: [{ item_id: 'lifting', score: 2.5 }] }]],
    [IPC_CHANNELS.reportsAddExercise, [{ modality: 'ared', duration_min: 10, astronaut_id: 'AST-02' }]],
    [IPC_CHANNELS.reportsAddExercise, [{ modality: 'yoga', duration_min: 10 }]],
    [IPC_CHANNELS.reportsAddExercise, ['AST-02']],
    [IPC_CHANNELS.reportsDue, ['AST-02']],
    [IPC_CHANNELS.urgentSend, [{ red_flags: ['leg_weakness'], other_symptoms: [] }]],
    [IPC_CHANNELS.urgentSend, [{ crewId: 'AST-1', red_flags: [], other_symptoms: [] }]],
    [IPC_CHANNELS.contactsList, [{ astronautId: 'AST-02' }]],
    [IPC_CHANNELS.contactsLogOfficerCall, [{}]],
    [IPC_CHANNELS.contactsLogOfficerCall, [{ sessionId: null, text: 'call' }]],
  ])('rejects invalid input on %s %j without echoing it', async (channel, args) => {
    await expect(call(channel, ...args)).rejects.toThrow(/^invalid input$/);
    expect(repsCalls).toEqual([]);
  });
});

describe('crew channels', () => {
  it('lists DTOs and the next id', async () => {
    expect(await call(IPC_CHANNELS.crewList)).toEqual([
      { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'sim-01' },
      { id: 'AST-02', name: 'Marc Dubois', harnessId: 'sim-02' },
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

  it('signs out unlocked or locked, closes the live streams and lets another member in', async () => {
    await call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '1234' });
    expect(await call(IPC_CHANNELS.authSignOut)).toBeUndefined();
    expect(await call(IPC_CHANNELS.authCurrent)).toBeNull();
    expect(repsCalls).toContainEqual(['closeAll']);
    await call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '1234' });
    await call(IPC_CHANNELS.authLock);
    expect(await call(IPC_CHANNELS.authSignOut)).toBeUndefined();
    expect(await call(IPC_CHANNELS.authCurrent)).toBeNull();
    expect(await call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-02', pin: '1234' })).toEqual({ ok: true, crewId: 'AST-02' });
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

  it('generates the Houston report for the unlocked astronaut and saves it where the dialog says, never as a rejection', async () => {
    expect(await call(IPC_CHANNELS.houstonReportGenerate)).toEqual({ status: 'error', reason: 'locked' });
    await unlock();
    expect(await call(IPC_CHANNELS.houstonReportGenerate)).toEqual({ status: 'saved', fileName: 'houston-report.pdf', opened: true });
    expect(backend.calls).toEqual([['houstonReport', 'AST-01']]);
    expect(saved).toEqual({ dialogs: ['houston-report.pdf'], written: [[join('chosen', 'houston-report.pdf'), 1]] });
  });

  it('adds the crew first name to first-day', async () => {
    await unlock();
    expect(await call(IPC_CHANNELS.viewsFirstDay)).toEqual({
      schema_version: '1.0.0',
      view: 'firstDay',
      crewFirstName: 'Elena',
    });
  });

  it.each([IPC_CHANNELS.viewsStrip, IPC_CHANNELS.viewsToday, IPC_CHANNELS.viewsBaseline, IPC_CHANNELS.viewsFirstDay, IPC_CHANNELS.viewsCordProtocols, IPC_CHANNELS.viewsCordHistory, IPC_CHANNELS.viewsHouston, IPC_CHANNELS.viewsOutbox])(
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

describe('chat channels', () => {
  const forbiddenEvent: IpcEventLike = { sender, senderFrame: null };
  const ask = { conversationId: CHAT_CONVERSATION, question: 'How am I?' };

  it('asks through the calling window and returns the subscription id', async () => {
    expect(await call(IPC_CHANNELS.chatAsk, ask)).toBe(SUBSCRIPTION);
    expect(chatCalls).toEqual([['ask', sender, ask]]);
  });

  it('cancels through the calling window', async () => {
    expect(await call(IPC_CHANNELS.chatCancel, { subscriptionId: SUBSCRIPTION })).toBeUndefined();
    expect(chatCalls).toEqual([['cancel', sender, SUBSCRIPTION]]);
  });

  it('forgets a conversation', async () => {
    expect(await call(IPC_CHANNELS.chatForget, { conversationId: CHAT_CONVERSATION })).toBeUndefined();
    expect(chatCalls).toEqual([['forget', CHAT_CONVERSATION]]);
  });

  it('does not reach the service from an untrusted sender', async () => {
    await expect(ipc.invoke(IPC_CHANNELS.chatAsk, forbiddenEvent, ask)).rejects.toThrow('forbidden');
    expect(chatCalls).toEqual([]);
  });

  it('passes "locked" through without logging it, and hides any other failure behind "unavailable"', async () => {
    chatFailWith = new ViewsError('locked');
    await expect(call(IPC_CHANNELS.chatAsk, ask)).rejects.toThrow(/^locked$/);
    expect(logged).toEqual([]);
    const bug = new Error('boom C:\\secret');
    chatFailWith = bug;
    await expect(call(IPC_CHANNELS.chatAsk, ask)).rejects.toThrow(/^unavailable$/);
    expect(logged).toEqual([bug]);
  });

  it('stops every live answer when the app is locked or signed out', async () => {
    await call(IPC_CHANNELS.authLock);
    await call(IPC_CHANNELS.authSignOut);
    expect(chatCalls).toEqual([['stopAll'], ['stopAll']]);
  });
});

describe('profile, daily context, devices and harness channels', () => {
  const unlock = () => call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '1234' });
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

  it("derives today's daily context after a successful sign-in, not after a wrong PIN", async () => {
    await call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '0000' });
    expect(ensured).toEqual([]);
    await unlock();
    expect(ensured).toEqual(['AST-01']);
  });

  it('reads and writes the unlocked astronaut profile', async () => {
    await unlock();
    expect(await call(IPC_CHANNELS.profileGet)).toBeNull();
    const result = await call(IPC_CHANNELS.profileUpdate, PROFILE_FIELDS);
    expect(result).toMatchObject({ ok: true, value: { astronaut_id: 'AST-01', launch_date: '2026-11-01' } });
    await settle();
    // The profile update derived today's context (FakeClock is 2026-11-05: mission day 5).
    expect(await call(IPC_CHANNELS.dailyContextToday)).toMatchObject({ phase: 'inflight', mission_day: 5 });
    expect(await call(IPC_CHANNELS.profileGet)).toMatchObject({ astronaut_id: 'AST-01' });
  });

  it("writes today's daily context and lists devices", async () => {
    await unlock();
    expect(await call(IPC_CHANNELS.dailyContextUpdateToday, CONTEXT_FIELDS)).toMatchObject({
      ok: true,
      value: { astronaut_id: 'AST-01', date: '2026-11-05', mission_day: 5 },
    });
    records.devices = [{ device_id: 'wb-01' } as never];
    expect(await call(IPC_CHANNELS.devicesList)).toEqual([{ device_id: 'wb-01' }]);
  });

  it('returns a refused write as a result, not a rejection', async () => {
    await unlock();
    records.writeAnswer = { ok: false, reason: 'invalid', fields: [{ pointer: '/date_of_birth', message: 'x' }] };
    expect(await call(IPC_CHANNELS.profileUpdate, PROFILE_FIELDS)).toEqual(records.writeAnswer);
  });

  it.each([IPC_CHANNELS.profileGet, IPC_CHANNELS.dailyContextToday, IPC_CHANNELS.devicesList])(
    'rejects %s with "locked" when nobody is unlocked',
    async (channel) => {
      await expect(call(channel)).rejects.toThrow(/^locked$/);
    },
  );

  it('answers the writes with locked when nobody is unlocked', async () => {
    expect(await call(IPC_CHANNELS.profileUpdate, PROFILE_FIELDS)).toEqual({ ok: false, reason: 'locked' });
    expect(await call(IPC_CHANNELS.dailyContextUpdateToday, CONTEXT_FIELDS)).toEqual({ ok: false, reason: 'locked' });
    expect(await call(IPC_CHANNELS.crewSetHarness, { harnessId: 'wb-07' })).toEqual({ ok: false, reason: 'locked' });
    expect(records.puts).toEqual([]);
  });

  it('turns a read failure into "unavailable"', async () => {
    await unlock();
    records.readFailure = new ViewsError('unavailable', { cause: new Error('down') });
    await expect(call(IPC_CHANNELS.devicesList)).rejects.toThrow(/^unavailable$/);
  });

  it('pairs and unpairs the unlocked member harness', async () => {
    await unlock();
    expect(await call(IPC_CHANNELS.crewSetHarness, { harnessId: 'wb-07' })).toEqual({
      ok: true,
      member: { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-07' },
    });
    expect(await call(IPC_CHANNELS.crewSetHarness, { harnessId: null })).toMatchObject({ ok: true, member: { harnessId: null } });
    expect(await call(IPC_CHANNELS.crewList)).toContainEqual({ id: 'AST-01', name: 'Elena Ruiz', harnessId: null });
  });
});

describe('session, report, urgent and contact channels', () => {
  const unlock = () => call(IPC_CHANNELS.authVerifyPin, { crewId: 'AST-01', pin: '1234' });
  const OTHER = '0f1e2d3c-4b5a-4968-8776-655443322110';

  it("starts a session on the unlocked member's harness and returns the result", async () => {
    await unlock();
    records.sendAnswer = { session: { session_id: SESSION }, state: 'starting', command: { command_id: OTHER } };
    expect(await call(IPC_CHANNELS.sessionsStart, { protocolId: 'spine_length_rest_v1' })).toEqual({
      ok: true,
      value: { sessionId: SESSION, state: 'starting' },
    });
    expect(records.sent[0]).toMatchObject({ path: '/ui/crew/AST-01/sessions', body: { device_id: 'sim-01' } });
  });

  it('sends the session commands for the unlocked astronaut', async () => {
    await unlock();
    records.sendAnswer = { command: { command_id: OTHER } };
    const sent = { ok: true, value: { commandId: OTHER } };
    expect(await call(IPC_CHANNELS.sessionsStop, { sessionId: SESSION, reason: 'aborted_pain', repIndex: 4 })).toEqual(sent);
    expect(await call(IPC_CHANNELS.sessionsMarkArtifact, { sessionId: SESSION, repIndex: null })).toEqual(sent);
    records.sessionStreams.set(SESSION, [{ streamId: 'emg_lat_left', kind: 'emg' }]);
    expect(await call(IPC_CHANNELS.sessionsCalibrateMvc, { sessionId: SESSION })).toEqual(sent);
    records.sendAnswer = { session: SESSION, state: 'ended' };
    expect(await call(IPC_CHANNELS.sessionsReprocess, { sessionId: SESSION })).toEqual({ ok: true, value: { sessionId: SESSION } });
    expect(records.sent.map((r) => r.path)).toEqual([
      `/ui/sessions/${SESSION}/stop`,
      `/ui/sessions/${SESSION}/events`,
      `/ui/sessions/${SESSION}/calibrations`,
      `/ui/sessions/${SESSION}/reprocess`,
    ]);
  });

  it('saves the reports and reads what is due', async () => {
    await unlock();
    records.sendAnswer = { report: { report_id: OTHER }, assessment: null, assessment_skipped: 'no_profile' };
    const saved = { ok: true, value: { reportId: OTHER, assessmentLevel: null, assessmentSkipped: 'no_profile', exercise: null } };
    expect(await call(IPC_CHANNELS.reportsSaveDaily, DAILY_FIELDS)).toEqual(saved);
    expect(await call(IPC_CHANNELS.reportsSaveOswestry, { items: [{ item_id: 'lifting', score: 2 }] })).toEqual(saved);
    expect(await call(IPC_CHANNELS.reportsDue)).toEqual(records.due);
  });

  it('retries an exercise on the unlocked astronaut, and says when today has no context', async () => {
    await unlock();
    const entry = { modality: 'ared', duration_min: 20 };
    expect(await call(IPC_CHANNELS.reportsAddExercise, entry)).toEqual({ ok: false, reason: 'no_context' });
    expect(records.puts).toEqual([]);
  });

  it('logs and lists contacts with the crew medical officer', async () => {
    await unlock();
    records.sendAnswer = { contact_id: OTHER, at: '2026-11-05T10:00:00Z' };
    expect(await call(IPC_CHANNELS.contactsLogOfficerCall, { sessionId: null })).toEqual({
      ok: true,
      value: { contactId: OTHER, at: '2026-11-05T10:00:00Z', sessionId: null },
    });
    records.contacts = [{ contactId: OTHER, at: '2026-11-05T10:00:00Z', sessionId: null }];
    expect(await call(IPC_CHANNELS.contactsList)).toEqual(records.contacts);
  });

  it('sends an urgent report without a PIN for a crew member, but for nobody unknown', async () => {
    records.sendAnswer = { report: { report_id: OTHER }, assessment: { level: 'red' }, assessment_skipped: null };
    const input = { crewId: 'AST-02', red_flags: ['leg_weakness'], other_symptoms: [] };
    expect(await call(IPC_CHANNELS.urgentSend, input)).toMatchObject({ ok: true, value: { assessmentLevel: 'red' } });
    expect(await call(IPC_CHANNELS.urgentSend, { ...input, crewId: 'AST-09' })).toEqual({ ok: false, reason: 'locked' });
    expect(records.sent.map((r) => r.astronautId)).toEqual(['AST-02']);
  });

  it.each([IPC_CHANNELS.reportsDue, IPC_CHANNELS.contactsList])('rejects %s with "locked" when nobody is unlocked', async (channel) => {
    await expect(call(channel)).rejects.toThrow(/^locked$/);
  });

  it('answers the writes with locked when nobody is unlocked', async () => {
    const locked = { ok: false, reason: 'locked' };
    expect(await call(IPC_CHANNELS.sessionsStart, { protocolId: 'spine_length_rest_v1' })).toEqual(locked);
    expect(await call(IPC_CHANNELS.sessionsStop, { sessionId: SESSION, reason: 'completed' })).toEqual(locked);
    expect(await call(IPC_CHANNELS.reportsSaveDaily, DAILY_FIELDS)).toEqual(locked);
    expect(await call(IPC_CHANNELS.reportsAddExercise, { modality: 'ared', duration_min: 20 })).toEqual(locked);
    expect(await call(IPC_CHANNELS.contactsLogOfficerCall, { sessionId: SESSION })).toEqual(locked);
    expect(records.sent).toEqual([]);
  });
});
