import type { BrowserWindowConstructorOptions } from 'electron';
import { describe, expect, it } from 'vitest';

import { createAppUrlPolicy } from './security/app-url';
import {
  createMainWindow,
  hardenSession,
  hardenWebContents,
  installWebContentsGuard,
  type HeadersListener,
  type NavigationEventLike,
} from './window';

const policy = createAppUrlPolicy({ rendererFile: '/app/out/renderer/index.html', platform: 'linux' });
const APP = 'file:///app/out/renderer/index.html';

class FakeEvent implements NavigationEventLike {
  defaultPrevented = false;
  constructor(readonly url = '') {}
  preventDefault(): void {
    this.defaultPrevented = true;
  }
}

/** Records what the code under test registers on a webContents, so the test can fire Electron's events. */
class FakeWebContents {
  openHandler: (() => { action: 'deny' }) | null = null;
  readonly listeners = new Map<string, (event: FakeEvent) => void>();
  setWindowOpenHandler(handler: () => { action: 'deny' }): void {
    this.openHandler = handler;
  }
  on(event: string, listener: (event: FakeEvent) => void): this {
    this.listeners.set(event, listener);
    return this;
  }
  fire(name: string, url?: string): FakeEvent {
    const event = new FakeEvent(url);
    const listener = this.listeners.get(name);
    if (!listener) throw new Error(`no ${name} listener`);
    listener(event);
    return event;
  }
}

describe('hardenWebContents', () => {
  it('denies every window.open / target=_blank', () => {
    const wc = new FakeWebContents();
    hardenWebContents(wc, policy);
    expect(wc.openHandler?.()).toEqual({ action: 'deny' });
  });

  it.each(['will-navigate', 'will-redirect'])('%s: blocks foreign URLs, allows the app itself', (name) => {
    const wc = new FakeWebContents();
    hardenWebContents(wc, policy);
    expect(wc.fire(name, 'https://example.com/').defaultPrevented).toBe(true);
    expect(wc.fire(name, 'file:///etc/passwd').defaultPrevented).toBe(true);
    expect(wc.fire(name, `${APP}#/settings`).defaultPrevented).toBe(false);
  });

  it('blocks <webview> attachment', () => {
    const wc = new FakeWebContents();
    hardenWebContents(wc, policy);
    expect(wc.fire('will-attach-webview').defaultPrevented).toBe(true);
  });
});

describe('installWebContentsGuard', () => {
  it('hardens every webContents the app creates', () => {
    let created: ((event: unknown, wc: FakeWebContents) => void) | undefined;
    const app = {
      on(event: 'web-contents-created', listener: (event: unknown, wc: FakeWebContents) => void) {
        expect(event).toBe('web-contents-created');
        created = listener;
        return app;
      },
    };
    installWebContentsGuard(app, policy);
    const wc = new FakeWebContents();
    created?.({}, wc);
    expect(wc.openHandler?.()).toEqual({ action: 'deny' });
    expect(wc.fire('will-navigate', 'https://example.com/').defaultPrevented).toBe(true);
  });
});

class FakeSession {
  requestHandler: ((wc: unknown, permission: string, callback: (granted: boolean) => void) => void) | null = null;
  checkHandler: (() => boolean) | null = null;
  headersListener: HeadersListener | null = null;
  readonly webRequest = {
    onHeadersReceived: (listener: HeadersListener) => {
      this.headersListener = listener;
    },
  };
  setPermissionRequestHandler(handler: (wc: unknown, permission: string, callback: (granted: boolean) => void) => void) {
    this.requestHandler = handler;
  }
  setPermissionCheckHandler(handler: () => boolean) {
    this.checkHandler = handler;
  }
}

describe('hardenSession', () => {
  it('denies every permission request and check', () => {
    const session = new FakeSession();
    hardenSession(session, 'default-src x');
    const answers: boolean[] = [];
    session.requestHandler?.({}, 'media', (granted) => answers.push(granted));
    session.requestHandler?.({}, 'notifications', (granted) => answers.push(granted));
    expect(answers).toEqual([false, false]);
    expect(session.checkHandler?.()).toBe(false);
  });

  it('sets the CSP response header, replacing any CSP the response carried', () => {
    const session = new FakeSession();
    hardenSession(session, "default-src 'self'");
    let headers: Record<string, string[] | string> | undefined;
    session.headersListener?.(
      { responseHeaders: { 'content-type': ['text/html'], 'content-security-policy': ['default-src *'] } },
      (response) => (headers = response.responseHeaders),
    );
    expect(headers).toEqual({ 'content-type': ['text/html'], 'Content-Security-Policy': ["default-src 'self'"] });
  });

  it('sets the CSP header when the response had no headers', () => {
    const session = new FakeSession();
    hardenSession(session, "default-src 'self'");
    let headers: Record<string, string[] | string> | undefined;
    session.headersListener?.({}, (response) => (headers = response.responseHeaders));
    expect(headers).toEqual({ 'Content-Security-Policy': ["default-src 'self'"] });
  });
});

describe('createMainWindow', () => {
  class FakeBrowserWindow {
    static last: FakeBrowserWindow | null = null;
    readyListener: (() => void) | null = null;
    loaded: string | null = null;
    shown = false;
    constructor(readonly options: BrowserWindowConstructorOptions) {
      FakeBrowserWindow.last = this;
    }
    once(_event: 'ready-to-show', listener: () => void) {
      this.readyListener = listener;
      return this;
    }
    show() {
      this.shown = true;
    }
    loadURL(url: string) {
      this.loaded = url;
      return Promise.resolve();
    }
  }

  it('creates a sandboxed, isolated window with the preload, loads the app URL and shows when ready', () => {
    const win = createMainWindow(FakeBrowserWindow, { preloadPath: '/app/out/preload/index.cjs', policy, devTools: false });
    expect(win).toBe(FakeBrowserWindow.last);
    expect(win.options.show).toBe(false);
    expect(win.options.webPreferences).toEqual({
      preload: '/app/out/preload/index.cjs',
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      spellcheck: false,
      devTools: false,
    });
    expect(win.loaded).toBe(APP);
    expect(win.shown).toBe(false);
    win.readyListener?.();
    expect(win.shown).toBe(true);
  });

  it('enables devtools only when asked (unpackaged builds)', () => {
    const win = createMainWindow(FakeBrowserWindow, { preloadPath: '/p.cjs', policy, devTools: true });
    expect(win.options.webPreferences?.devTools).toBe(true);
  });
});
