/**
 * Sandboxed preload (bundled to CommonJS). Exposes only `window.asclepius`: one function per IPC channel,
 * each forwarding exactly one argument (or none). No Electron object or `ipcRenderer` reaches the renderer;
 * all validation happens in the main process.
 */
import { contextBridge, ipcRenderer } from 'electron';

import type { AsclepiusApi } from '../shared/api';
import { IPC_CHANNELS } from '../shared/ipc-channels';

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
});

contextBridge.exposeInMainWorld('asclepius', api);
