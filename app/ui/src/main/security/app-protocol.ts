/**
 * The `app:` scheme that serves the built renderer (out/renderer) to the main window.
 *
 * Why not file://: with the GrantFileProtocolExtraPrivileges fuse off (scripts/after-pack.cjs) the packaged app
 * could not load out/renderer/index.html from app.asar over file:// (ERR_FILE_NOT_FOUND, blank window), and Vite's
 * `<script type="module" crossorigin>` needs a real origin anyway (file:// is origin "null"). A standard, secure
 * scheme gives the renderer that origin (`app://asclepius`) for module scripts, `'self'` in the CSP and the
 * IPC sender checks.
 *
 * The handler serves only files inside the renderer folder: every path segment must be a plain file name
 * (no dot-leading segment, encoded or raw separators, drive letters or empty segments), the resolved path is
 * checked to stay under the root, unknown file types and directories are 404, and every response carries the
 * CSP and `nosniff` headers. (session.webRequest also sets the CSP on app:// responses in Electron 45, checked by
 * e2e/packaged.spec.ts; the handler does not rely on that.)
 */
import { readFile } from 'node:fs/promises';
import path from 'node:path';

import { APP_SCHEME, isAppOrigin, parseUrl } from './app-url';

/** For `protocol.registerSchemesAsPrivileged`, which must run before the app is ready. */
export const APP_SCHEME_PRIVILEGES = [
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
] as const;

const CONTENT_TYPES: Readonly<Record<string, string>> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};

const SEGMENT = /^[A-Za-z0-9_][A-Za-z0-9._-]*$/;
const ENCODED_SEPARATOR_OR_NUL = /%(2f|5c|00)/i;

export function contentTypeFor(file: string): string | null {
  return CONTENT_TYPES[path.extname(file).toLowerCase()] ?? null;
}

/** Maps an app:// URL to a file under `root`, or null when it is not one we serve. */
export function resolveAppFile(root: string, url: string): string | null {
  const parsed = parseUrl(url);
  if (parsed === null || !isAppOrigin(parsed) || parsed.port !== '' || parsed.search !== '') return null;
  const rawPath = parsed.pathname;
  if (rawPath.includes('\\') || ENCODED_SEPARATOR_OR_NUL.test(rawPath)) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(rawPath);
  } catch {
    return null;
  }
  const segments = decoded.split('/').slice(1);
  if (!decoded.startsWith('/') || !segments.every((segment) => SEGMENT.test(segment))) return null;
  const base = path.resolve(root);
  const file = path.resolve(base, ...segments);
  const relative = path.relative(base, file);
  if (relative === '' || relative.startsWith('..') || path.isAbsolute(relative)) return null;
  return file;
}

export interface AppProtocolOptions {
  /** Absolute path of out/renderer. */
  root: string;
  /** Sent as the Content-Security-Policy header on every response. */
  csp: string;
}

export interface AppRequestLike {
  readonly url: string;
  readonly method: string;
}

export function createAppProtocolHandler({ root, csp }: AppProtocolOptions): (request: AppRequestLike) => Promise<Response> {
  const respond = (status: number, body: Uint8Array | null = null, contentType?: string): Response => {
    const headers: Record<string, string> = { 'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff' };
    if (contentType !== undefined) headers['Content-Type'] = contentType;
    return new Response(body, { status, headers });
  };

  return async ({ url, method }) => {
    if (method !== 'GET' && method !== 'HEAD') return respond(405);
    const file = resolveAppFile(root, url);
    const contentType = file === null ? null : contentTypeFor(file);
    if (file === null || contentType === null) return respond(404);
    try {
      return respond(200, new Uint8Array(await readFile(file)), contentType);
    } catch {
      return respond(404); // missing, or a directory (no listings)
    }
  };
}
