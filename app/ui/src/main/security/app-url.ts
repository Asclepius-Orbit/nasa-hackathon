/**
 * Which URLs are "the app". Built app: exactly app://asclepius/index.html (any #hash, no ?query), served by the
 * privileged `app:` scheme (see app-protocol.ts). Dev: the electron-vite dev server origin.
 * Used for navigation guards and IPC sender checks.
 */

export const APP_SCHEME = 'app';
export const APP_HOST = 'asclepius';
export const APP_ENTRY_URL = `${APP_SCHEME}://${APP_HOST}/index.html`;

export interface AppUrlPolicy {
  /** What the main window loads. */
  entryUrl: string;
  isAppUrl(url: string): boolean;
}

export interface AppUrlPolicyOptions {
  /** `ELECTRON_RENDERER_URL`; only ever passed by dev builds (`import.meta.env.DEV`). */
  devServerUrl?: string | undefined;
}

export function parseUrl(url: string): URL | null {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

/**
 * True for `app://asclepius/...` with no credentials or port. `app:` is not a WHATWG special scheme, so Node
 * keeps the host as written (no lowercasing) and reports origin "null"; compare the parts instead.
 */
export function isAppOrigin(url: URL): boolean {
  return (
    url.protocol === `${APP_SCHEME}:` && url.host === APP_HOST && url.username === '' && url.password === ''
  );
}

export function createAppUrlPolicy({ devServerUrl }: AppUrlPolicyOptions): AppUrlPolicy {
  if (devServerUrl !== undefined && devServerUrl !== '') {
    const origin = new URL(devServerUrl).origin;
    return { entryUrl: devServerUrl, isAppUrl: (url) => parseUrl(url)?.origin === origin };
  }
  return {
    entryUrl: APP_ENTRY_URL,
    isAppUrl(url) {
      const parsed = parseUrl(url);
      return parsed !== null && isAppOrigin(parsed) && parsed.pathname === '/index.html' && parsed.search === '';
    },
  };
}

export interface SenderFrameLike {
  readonly url: string;
  readonly parent: unknown;
}

export interface IpcEventLike {
  readonly senderFrame: SenderFrameLike | null;
}

/** Trust only the app's own top-level frame. */
export function createSenderGuard(policy: AppUrlPolicy): (event: IpcEventLike) => boolean {
  return ({ senderFrame }) => senderFrame !== null && senderFrame.parent === null && policy.isAppUrl(senderFrame.url);
}
