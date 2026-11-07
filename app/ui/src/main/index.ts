// Electron entry: wiring only. Logic lives in tested modules (window, ipc, auth, store, security).
//
// Dev-only behaviour (dev server URL, relaxed CSP, DevTools, default menu, demo crew) is gated on build-time
// constants (`import.meta.env`), never on Electron's "is packaged" flag, which only reflects the exe name. Production
// bundles therefore do not contain that code at all; `pnpm check:bundle` verifies it.
import { join } from 'node:path';

import { app, BrowserWindow, ipcMain, Menu, protocol, safeStorage, session } from 'electron';

import { authStateSchema, createAuthService } from './auth/auth-service';
import { createPinHasher } from './auth/pin';
import { registerIpc } from './ipc';
import { APP_SCHEME_PRIVILEGES, createAppProtocolHandler } from './security/app-protocol';
import { APP_SCHEME, createAppUrlPolicy, createSenderGuard } from './security/app-url';
import { devCsp, PROD_CSP } from './security/csp';
import { createCrewStore, crewFileSchema } from './store/crew-store';
import { DEMO_CREW } from './store/demo-crew';
import { createEncryptedJsonFile } from './store/encrypted-file';
import { createSettingsStore } from './store/settings-store';
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
    const crew = createCrewStore({
      file: createEncryptedJsonFile({ path: join(userData, 'crew.bin'), schema: crewFileSchema, safeStorage }),
      hasher,
      seed: seedDemoCrew ? DEMO_CREW : null,
    });
    const auth = createAuthService({
      crew,
      hasher,
      stateFile: createEncryptedJsonFile({ path: join(userData, 'auth-state.bin'), schema: authStateSchema, safeStorage }),
      now: Date.now,
    });
    const settings = createSettingsStore({ path: join(userData, 'settings.json') });
    registerIpc(ipcMain, { crew, auth, settings }, createSenderGuard(policy));

    openWindow();
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) openWindow();
    });
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}
