/**
 * Window and session hardening. Takes Electron objects through narrow interfaces (no runtime `electron`
 * import here) so the policy is unit-testable; src/main/index.ts passes the real ones.
 *
 * In-app routing is hash-based, and hash changes do not emit `will-navigate`; the allow-list also accepts
 * any #hash on the app URL, so routing keeps working either way.
 */
import type { BrowserWindowConstructorOptions } from 'electron';

import type { AppUrlPolicy } from './security/app-url';

export interface NavigationEventLike {
  readonly url: string;
  preventDefault(): void;
}

export interface WebContentsLike {
  setWindowOpenHandler(handler: () => { action: 'deny' }): void;
  on(event: 'will-navigate' | 'will-redirect', listener: (event: NavigationEventLike) => void): unknown;
  on(event: 'will-attach-webview', listener: (event: { preventDefault(): void }) => void): unknown;
}

export function hardenWebContents(contents: WebContentsLike, policy: AppUrlPolicy): void {
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  const blockForeign = (event: NavigationEventLike): void => {
    if (!policy.isAppUrl(event.url)) event.preventDefault();
  };
  contents.on('will-navigate', blockForeign);
  contents.on('will-redirect', blockForeign);
  contents.on('will-attach-webview', (event) => {
    event.preventDefault();
  });
}

export interface AppLike<W extends WebContentsLike> {
  on(event: 'web-contents-created', listener: (event: unknown, contents: W) => void): unknown;
}

/** Covers every webContents, including the main window's and any created later. */
export function installWebContentsGuard<W extends WebContentsLike>(app: AppLike<W>, policy: AppUrlPolicy): void {
  app.on('web-contents-created', (_event, contents) => {
    hardenWebContents(contents, policy);
  });
}

type ResponseHeaders = Record<string, string[] | string>;
export type HeadersListener = (
  details: { responseHeaders?: ResponseHeaders },
  callback: (response: { responseHeaders: ResponseHeaders }) => void,
) => void;

export interface SessionLike {
  setPermissionRequestHandler(
    handler: (contents: unknown, permission: string, callback: (granted: boolean) => void) => void,
  ): void;
  setPermissionCheckHandler(handler: () => boolean): void;
  readonly webRequest: { onHeadersReceived(listener: HeadersListener): void };
}

export function hardenSession(session: SessionLike, csp: string): void {
  session.setPermissionRequestHandler((_contents, _permission, callback) => {
    callback(false);
  });
  session.setPermissionCheckHandler(() => false);
  session.webRequest.onHeadersReceived((details, callback) => {
    const headers = Object.fromEntries(
      Object.entries(details.responseHeaders ?? {}).filter(([name]) => name.toLowerCase() !== 'content-security-policy'),
    );
    callback({ responseHeaders: { ...headers, 'Content-Security-Policy': [csp] } });
  });
}

export interface BrowserWindowLike {
  once(event: 'ready-to-show', listener: () => void): unknown;
  show(): void;
  loadURL(url: string): Promise<void>;
}

export interface MainWindowOptions {
  preloadPath: string;
  policy: AppUrlPolicy;
  /** Dev builds only (`import.meta.env.DEV`). */
  devTools: boolean;
}

export function createMainWindow<W extends BrowserWindowLike>(
  BrowserWindow: new (options: BrowserWindowConstructorOptions) => W,
  { preloadPath, policy, devTools }: MainWindowOptions,
): W {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      spellcheck: false,
      devTools,
    },
  });
  win.once('ready-to-show', () => {
    win.show();
  });
  void win.loadURL(policy.entryUrl);
  return win;
}
