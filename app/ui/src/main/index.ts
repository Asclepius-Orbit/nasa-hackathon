// Electron entry: wiring only. Logic lives in tested modules (window, ipc, auth, store, security).
//
// Dev-only behaviour (dev server URL, relaxed CSP, DevTools, default menu, demo crew) is gated on build-time
// constants (`import.meta.env`), never on Electron's "is packaged" flag, which only reflects the exe name. Production
// bundles therefore do not contain that code at all; `pnpm check:bundle` verifies it.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { app, BrowserWindow, ipcMain, Menu, protocol, safeStorage, session } from 'electron';

import { authStateSchema, createAuthService } from './auth/auth-service';
import { createPinHasher } from './auth/pin';
import { registerIpc } from './ipc';
import { createDailyContextService } from './profile/daily-context-service';
import { createDevicesService } from './profile/devices-service';
import { createHarnessService } from './profile/harness-service';
import { createProfileService } from './profile/profile-service';
import { createContactsService } from './reports/contacts-service';
import { createReportsService } from './reports/reports-service';
import { createUrgentService } from './reports/urgent-service';
import { APP_SCHEME_PRIVILEGES, createAppProtocolHandler } from './security/app-protocol';
import { APP_SCHEME, createAppUrlPolicy, createSenderGuard } from './security/app-url';
import { devCsp, PROD_CSP } from './security/csp';
import { createSessionsService } from './sessions/sessions-service';
import { createCrewRegistry, withCrewRegistry } from './store/crew-registry';
import { createCrewStore, crewFileSchema } from './store/crew-store';
import { DEMO_CREW } from './store/demo-crew';
import { createEncryptedJsonFile } from './store/encrypted-file';
import { createSettingsStore } from './store/settings-store';
import { resolveBackendToken } from './views/backend-auth';
import { BACKEND_URL_ENV, resolveBackendUrl } from './views/backend-url';
import { createRepsService } from './views/reps-service';
import { createRecordsClient } from './views/records-client';
import { createRepsStreamClient } from './views/reps-stream';
import { createViewsClient } from './views/views-client';
import { createViewsService } from './views/views-service';
import { createWriteClient } from './views/write-client';
import { createMainWindow, hardenSession, installWebContentsGuard } from './window';

const devServerUrl = import.meta.env.DEV ? process.env['ELECTRON_RENDERER_URL'] : undefined;
const csp = import.meta.env.DEV ? devCsp() : PROD_CSP;
const seedDemoCrew = import.meta.env.DEV || import.meta.env.MODE === 'e2e';
const policy = createAppUrlPolicy({ devServerUrl });
const preloadPath = join(import.meta.dirname, '../preload/index.cjs');
const rendererRoot = join(import.meta.dirname, '../renderer');

app.enableSandbox();
protocol.registerSchemesAsPrivileged([...APP_SCHEME_PRIVILEGES]);

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let mainWindow: BrowserWindow | null = null;

  const openWindow = (): void => {
    mainWindow = createMainWindow(BrowserWindow, { preloadPath, policy, devTools: import.meta.env.DEV });
    mainWindow.on('closed', () => {
      mainWindow = null;
    });
  };

  app.on('second-instance', () => {
    if (mainWindow === null) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  installWebContentsGuard(app, policy);

  void app.whenReady().then(() => {
    if (!import.meta.env.DEV) Menu.setApplicationMenu(null);
    hardenSession(session.defaultSession, csp);
    // The handler sets the CSP header itself: custom-protocol responses may bypass session.webRequest.
    session.defaultSession.protocol.handle(APP_SCHEME, createAppProtocolHandler({ root: rendererRoot, csp }));

    const userData = app.getPath('userData');
    const hasher = createPinHasher();
    const crewStore = createCrewStore({
      file: createEncryptedJsonFile({ path: join(userData, 'crew.bin'), schema: crewFileSchema, safeStorage }),
      hasher,
      seed: seedDemoCrew ? DEMO_CREW : null,
    });
    const auth = createAuthService({
      crew: crewStore,
      hasher,
      stateFile: createEncryptedJsonFile({ path: join(userData, 'auth-state.bin'), schema: authStateSchema, safeStorage }),
      now: Date.now,
    });
    const settings = createSettingsStore({ path: join(userData, 'settings.json') });
    // Backend URL: main-process configuration only (never a renderer setting), loopback hosts only.
    const backendUrl = resolveBackendUrl(process.env[BACKEND_URL_ENV], () => {
      console.error(`${BACKEND_URL_ENV} ignored: only http(s) loopback URLs are accepted`);
    });
    // The /ui token stays in this process: it is never handed to the renderer.
    const token = resolveBackendToken(process.env, (path) => readFileSync(path, 'utf8'), console.error);
    // The store owns the crew; the backend only learns each id (never a name or PIN), retried while it is down.
    const crew = withCrewRegistry(crewStore, createCrewRegistry({ baseUrl: backendUrl, token, logError: console.error }));
    void crew.list();
    const views = createViewsService({ auth, crew, client: createViewsClient({ baseUrl: backendUrl, token }) });
    const reps = createRepsService({
      auth,
      views,
      streams: createRepsStreamClient({ baseUrl: backendUrl, token, logError: console.error }),
      logError: console.error,
    });
    const records = createRecordsClient({ baseUrl: backendUrl, token });
    const writer = createWriteClient({ baseUrl: backendUrl, token, logError: console.error });
    const dailyContext = createDailyContextService({ auth, records, writer, now: Date.now, logError: console.error });
    const profile = createProfileService({ auth, records, writer, dailyContext, logError: console.error });
    const devices = createDevicesService({ auth, records });
    const harness = createHarnessService({ auth, crew });
    const sessions = createSessionsService({ auth, crew, records, writer, logError: console.error });
    const reports = createReportsService({ auth, records, writer, dailyContext });
    const urgent = createUrgentService({ auth, crew, writer, now: Date.now });
    const contacts = createContactsService({ auth, records, writer });
    registerIpc(
      ipcMain,
      { crew, auth, settings, views, reps, profile, dailyContext, devices, harness, sessions, reports, urgent, contacts },
      createSenderGuard(policy),
    );

    openWindow();
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) openWindow();
    });
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}
