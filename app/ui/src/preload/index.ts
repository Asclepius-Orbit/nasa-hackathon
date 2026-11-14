/**
 * Sandboxed preload (bundled to CommonJS). Exposes only `window.asclepius`: one function per IPC channel,
 * each forwarding exactly one argument (or none). No Electron object or `ipcRenderer` reaches the renderer;
 * all validation happens in the main process.
 */
import { contextBridge, ipcRenderer } from 'electron';

import type { AsclepiusApi } from '../shared/api';
import { createChatBridge } from '../shared/chat-bridge';
import { IPC_CHANNELS } from '../shared/ipc-channels';
import { createRepsBridge } from '../shared/reps-bridge';
import { viewsCall } from '../shared/views-errors';

const api: AsclepiusApi = Object.freeze({
  crew: Object.freeze({
    list: () => ipcRenderer.invoke(IPC_CHANNELS.crewList),
    nextId: () => ipcRenderer.invoke(IPC_CHANNELS.crewNextId),
    create: (input) => ipcRenderer.invoke(IPC_CHANNELS.crewCreate, input),
    setHarness: (input) => ipcRenderer.invoke(IPC_CHANNELS.crewSetHarness, input),
  } satisfies AsclepiusApi['crew']),
  auth: Object.freeze({
    verifyPin: (input) => ipcRenderer.invoke(IPC_CHANNELS.authVerifyPin, input),
    lock: () => ipcRenderer.invoke(IPC_CHANNELS.authLock),
    signOut: () => ipcRenderer.invoke(IPC_CHANNELS.authSignOut),
    current: () => ipcRenderer.invoke(IPC_CHANNELS.authCurrent),
  } satisfies AsclepiusApi['auth']),
  settings: Object.freeze({
    get: () => ipcRenderer.invoke(IPC_CHANNELS.settingsGet),
    update: (patch) => ipcRenderer.invoke(IPC_CHANNELS.settingsUpdate, patch),
  } satisfies AsclepiusApi['settings']),
  // Every view call rejects with an Error whose message is exactly a ViewsErrorCode (see shared/views-errors).
  views: Object.freeze({
    strip: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsStrip)),
    today: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsToday)),
    // Always exactly one argument: the main process rejects a missing or extra one.
    history: (range = {}) => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsHistory, range)),
    baseline: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsBaseline)),
    firstDay: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsFirstDay)),
    cordProtocols: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsCordProtocols)),
    cordHistory: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsCordHistory)),
    session: (sessionId) => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsSession, { sessionId })),
    houston: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsHouston)),
    outbox: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsOutbox)),
  } satisfies AsclepiusApi['views']),
  // Reads reject with a ViewsErrorCode; writes resolve with a WriteResult (never reject for a backend refusal).
  profile: Object.freeze({
    get: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.profileGet)),
    update: (fields) => ipcRenderer.invoke(IPC_CHANNELS.profileUpdate, fields),
  } satisfies AsclepiusApi['profile']),
  dailyContext: Object.freeze({
    today: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.dailyContextToday)),
    updateToday: (fields) => ipcRenderer.invoke(IPC_CHANNELS.dailyContextUpdateToday, fields),
  } satisfies AsclepiusApi['dailyContext']),
  devices: Object.freeze({
    list: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.devicesList)),
  } satisfies AsclepiusApi['devices']),
  sessions: Object.freeze({
    start: (input) => ipcRenderer.invoke(IPC_CHANNELS.sessionsStart, input),
    stop: (input) => ipcRenderer.invoke(IPC_CHANNELS.sessionsStop, input),
    markArtifact: (input) => ipcRenderer.invoke(IPC_CHANNELS.sessionsMarkArtifact, input),
    calibrateMvc: (input) => ipcRenderer.invoke(IPC_CHANNELS.sessionsCalibrateMvc, input),
    reprocess: (input) => ipcRenderer.invoke(IPC_CHANNELS.sessionsReprocess, input),
  } satisfies AsclepiusApi['sessions']),
  reports: Object.freeze({
    saveDaily: (fields) => ipcRenderer.invoke(IPC_CHANNELS.reportsSaveDaily, fields),
    saveOswestry: (input) => ipcRenderer.invoke(IPC_CHANNELS.reportsSaveOswestry, input),
    addExercise: (entry) => ipcRenderer.invoke(IPC_CHANNELS.reportsAddExercise, entry),
    due: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.reportsDue)),
  } satisfies AsclepiusApi['reports']),
  urgent: Object.freeze({
    send: (input) => ipcRenderer.invoke(IPC_CHANNELS.urgentSend, input),
  } satisfies AsclepiusApi['urgent']),
  contacts: Object.freeze({
    list: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.contactsList)),
    logOfficerCall: (input) => ipcRenderer.invoke(IPC_CHANNELS.contactsLogOfficerCall, input),
  } satisfies AsclepiusApi['contacts']),
  // Never rejects: the main process answers with a HoustonReportResult (saved, cancelled or error).
  houstonReport: Object.freeze({
    generate: () => ipcRenderer.invoke(IPC_CHANNELS.houstonReportGenerate),
  } satisfies AsclepiusApi['houstonReport']),
  // Chat with the agent, same bridge rules as the live counter below.
  chat: Object.freeze(createChatBridge(ipcRenderer)),
  // Live push. The bridge hands the callback plain events only: never `ipcRenderer` or Electron's event object.
  reps: Object.freeze(createRepsBridge(ipcRenderer)),
});

contextBridge.exposeInMainWorld('asclepius', api);
