/**
 * Which URLs are "the app". Built app: exactly the renderer's index.html (any #hash, no ?query).
 * Dev: the electron-vite dev server origin. Used for navigation guards and IPC sender checks.
 */
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export interface AppUrlPolicy {
  /** What the main window loads. */
  entryUrl: string;
  isAppUrl(url: string): boolean;
}

export interface AppUrlPolicyOptions {
  /** `ELECTRON_RENDERER_URL`; only pass it when the app is not packaged. */
  devServerUrl?: string | undefined;
  rendererFile: string;
  platform?: NodeJS.Platform;
}

function parse(url: string): URL | null {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

export function createAppUrlPolicy({
  devServerUrl,
  rendererFile,
  platform = process.platform,
}: AppUrlPolicyOptions): AppUrlPolicy {
  if (devServerUrl !== undefined && devServerUrl !== '') {
    const origin = new URL(devServerUrl).origin;
    return { entryUrl: devServerUrl, isAppUrl: (url) => parse(url)?.origin === origin };
  }

  const windows = platform === 'win32';
  const paths = windows ? path.win32 : path.posix;
  // NTFS paths are case-insensitive; POSIX ones are not.
  const canonical = (filePath: string): string => {
    const normalized = paths.normalize(filePath);
    return windows ? normalized.toLowerCase() : normalized;
  };
  const expected = canonical(rendererFile);

  return {
    entryUrl: pathToFileURL(rendererFile, { windows }).href,
    isAppUrl(url) {
      const parsed = parse(url);
      if (parsed?.protocol !== 'file:' || parsed.search !== '') return false;
      try {
        return canonical(fileURLToPath(parsed, { windows })) === expected;
      } catch {
        return false; // e.g. encoded path separators; a host turns into a UNC path, which never matches
      }
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
