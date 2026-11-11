/**
 * Credentials of the backend's /ui API, held by the main process only (never the renderer): the shared token, and with each
 * request the astronaut unlocked here. The backend refuses /ui without the token (401) and a request about another
 * astronaut than the one sent (403).
 *
 * The token is `ASCLEPIUS_BACKEND_TOKEN`, or the content of the file `ASCLEPIUS_BACKEND_TOKEN_FILE` (the backend writes one
 * next to its database when it has no token configured). Without either, requests go without a token and the backend
 * answers 401, which the clients report as `unavailable`.
 */
export const BACKEND_TOKEN_ENV = 'ASCLEPIUS_BACKEND_TOKEN';
export const BACKEND_TOKEN_FILE_ENV = 'ASCLEPIUS_BACKEND_TOKEN_FILE';

/** `authorization` and `x-astronaut-id`, each only when there is a value. */
export type AuthHeaders = Record<string, string>;

export function resolveBackendToken(
  env: Readonly<Record<string, string | undefined>>,
  readFile: (path: string) => string,
  onError: (message: string) => void,
): string | null {
  const direct = env[BACKEND_TOKEN_ENV]?.trim();
  if (direct) return direct;
  const path = env[BACKEND_TOKEN_FILE_ENV];
  if (!path) return null;
  try {
    return readFile(path).trim() || null;
  } catch {
    onError(`${BACKEND_TOKEN_FILE_ENV}: the token file cannot be read`);
    return null;
  }
}

/** The headers for one request: the token when there is one, and the astronaut when the request is about one. */
export function authHeaders(token: string | null, astronautId?: string): AuthHeaders {
  return {
    ...(token === null ? {} : { authorization: `Bearer ${token}` }),
    ...(astronautId === undefined ? {} : { 'x-astronaut-id': astronautId }),
  };
}
