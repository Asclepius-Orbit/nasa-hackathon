// Electron entry: wiring only. Logic lives in tested modules (window, ipc, auth, store, security).
import { join } from 'node:path';

import { app, BrowserWindow, ipcMain, Menu, safeStorage, session } from 'electron';

import { authStateSchema, createAuthService } from './auth/auth-service';
import { createPinHasher } from './auth/pin';
import { registerIpc } from './ipc';
import { createAppUrlPolicy, createSenderGuard } from './security/app-url';
import { cspFor } from './security/csp';
import { createCrewStore, crewFileSchema } from './store/crew-store';
import { createEncryptedJsonFile } from './store/encrypted-file';
import { createSettingsStore } from './store/settings-store';
import { createMainWindow, hardenSession, installWebContentsGuard } from './window';

const devServerUrl = app.isPackaged ? undefined : process.env['ELECTRON_RENDERER_URL'];
const policy = createAppUrlPolicy({
  devServerUrl,
  rendererFile: join(import.meta.dirname, '../renderer/index.html'),
});
const preloadPath = join(import.meta.dirname, '../preload/index.cjs');

app.enableSandbox();

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let mainWindow: BrowserWindow | null = null;

  const openWindow = (): void => {
    mainWindow = createMainWindow(BrowserWindow, { preloadPath, policy, devTools: !app.isPackaged });
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
    if (app.isPackaged) Menu.setApplicationMenu(null);
    hardenSession(session.defaultSession, cspFor(devServerUrl !== undefined && devServerUrl !== ''));

    const userData = app.getPath('userData');
    const hasher = createPinHasher();
    const crew = createCrewStore({
      file: createEncryptedJsonFile({ path: join(userData, 'crew.bin'), schema: crewFileSchema, safeStorage }),
      hasher,
      seed: !app.isPackaged,
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
