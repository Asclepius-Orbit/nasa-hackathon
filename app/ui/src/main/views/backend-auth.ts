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

/** The agent's token (`/chat`), read the same way from its own variables; held by the main process only. */
export const AGENT_TOKEN_ENV = 'ASCLEPIUS_AGENT_TOKEN';
export const AGENT_TOKEN_FILE_ENV = 'ASCLEPIUS_AGENT_TOKEN_FILE';

type Env = Readonly<Record<string, string | undefined>>;

function resolveToken(
  env: Env,
  names: { direct: string; file: string },
  readFile: (path: string) => string,
  onError: (message: string) => void,
): string | null {
  const direct = env[names.direct]?.trim();
  if (direct) return direct;
  const path = env[names.file];
  if (!path) return null;
  try {
    return readFile(path).trim() || null;
  } catch {
    onError(`${names.file}: the token file cannot be read`);
    return null;
  }
}

export function resolveBackendToken(env: Env, readFile: (path: string) => string, onError: (message: string) => void): string | null {
  return resolveToken(env, { direct: BACKEND_TOKEN_ENV, file: BACKEND_TOKEN_FILE_ENV }, readFile, onError);
}

export function resolveAgentToken(env: Env, readFile: (path: string) => string, onError: (message: string) => void): string | null {
  return resolveToken(env, { direct: AGENT_TOKEN_ENV, file: AGENT_TOKEN_FILE_ENV }, readFile, onError);
}

/** The headers for one request: the token when there is one, and the astronaut when the request is about one. */
export function authHeaders(token: string | null, astronautId?: string): AuthHeaders {
  return {
    ...(token === null ? {} : { authorization: `Bearer ${token}` }),
    ...(astronautId === undefined ? {} : { 'x-astronaut-id': astronautId }),
  };
}
