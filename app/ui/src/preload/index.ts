import { contextBridge } from 'electron';

import type { AsclepiusApi } from '../shared/api';

// P1 fills this in with the IPC-backed implementation.
const api = {} as AsclepiusApi;

contextBridge.exposeInMainWorld('asclepius', api);
