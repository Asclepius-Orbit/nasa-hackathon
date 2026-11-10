/**
 * Sandboxed preload (bundled to CommonJS). Exposes only `window.asclepius`: one function per IPC channel,
 * each forwarding exactly one argument (or none). No Electron object or `ipcRenderer` reaches the renderer;
 * all validation happens in the main process.
 */
import { contextBridge, ipcRenderer } from 'electron';

import type { AsclepiusApi } from '../shared/api';
import { IPC_CHANNELS } from '../shared/ipc-channels';
import { createRepsBridge } from '../shared/reps-bridge';
import { viewsCall } from '../shared/views-errors';

const api: AsclepiusApi = Object.freeze({
  crew: Object.freeze({
    list: () => ipcRenderer.invoke(IPC_CHANNELS.crewList),
    nextId: () => ipcRenderer.invoke(IPC_CHANNELS.crewNextId),
    create: (input) => ipcRenderer.invoke(IPC_CHANNELS.crewCreate, input),
  } satisfies AsclepiusApi['crew']),
  auth: Object.freeze({
    verifyPin: (input) => ipcRenderer.invoke(IPC_CHANNELS.authVerifyPin, input),
    lock: () => ipcRenderer.invoke(IPC_CHANNELS.authLock),
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
    session: (sessionId) => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsSession, { sessionId })),
    houston: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsHouston)),
    outbox: () => viewsCall(() => ipcRenderer.invoke(IPC_CHANNELS.viewsOutbox)),
  } satisfies AsclepiusApi['views']),
  // Live push. The bridge hands the callback plain events only: never `ipcRenderer` or Electron's event object.
  reps: Object.freeze(createRepsBridge(ipcRenderer)),
});

contextBridge.exposeInMainWorld('asclepius', api);
