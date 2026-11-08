/**
 * Where the Python backend (read API of the app) listens. The URL is configuration of the main process only:
 * it is not part of `Settings`, so the renderer can neither read nor change it. Only loopback hosts are accepted,
 * so a bad or tampered value cannot make the app send health views to another machine.
 */

export const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000';

/** Environment variable the main process reads (once, at startup) to override the default. */
export const BACKEND_URL_ENV = 'ASCLEPIUS_BACKEND_URL';

const ALLOWED_HOSTS: ReadonlySet<string> = new Set(['127.0.0.1', 'localhost']);

/**
 * Returns the normalised origin (`scheme://host[:port]`, no trailing slash) when `raw` is an http(s) URL to
 * 127.0.0.1 or localhost without credentials, path, query or fragment; otherwise `null`.
 * The host is compared after URL parsing, so `127.0.0.1@evil.com` or `127.0.0.1.evil.com` do not pass.
 */
export function parseBackendUrl(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (!ALLOWED_HOSTS.has(url.hostname)) return null;
  if (url.username !== '' || url.password !== '') return null;
  // An empty `?` or `#` parses to empty search/hash, so look at the raw text too.
  if (url.pathname !== '/' || /[?#]/u.test(raw)) return null;
  return url.origin;
}

/** The configured URL, or the default when none is set. An invalid value is reported and ignored (fail closed). */
export function resolveBackendUrl(raw: string | undefined, onInvalid: (raw: string) => void): string {
  if (raw === undefined || raw === '') return DEFAULT_BACKEND_URL;
  const parsed = parseBackendUrl(raw);
  if (parsed === null) {
    onInvalid(raw);
    return DEFAULT_BACKEND_URL;
  }
  return parsed;
}
