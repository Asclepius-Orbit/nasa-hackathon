/**
 * Where the spine agent (`spine-agent-http`) listens. Like the backend URL it is configuration of the main process only,
 * never a renderer setting, and only loopback hosts are accepted, so a tampered value cannot send questions elsewhere.
 */
import { parseBackendUrl } from '../views/backend-url';

export const DEFAULT_AGENT_URL = 'http://127.0.0.1:8003';

/** Environment variable the main process reads (once, at startup) to override the default. */
export const AGENT_URL_ENV = 'ASCLEPIUS_AGENT_URL';

/** The configured URL, or the default when none is set. An invalid value is reported and ignored (fail closed). */
export function resolveAgentUrl(raw: string | undefined, onInvalid: (raw: string) => void): string {
  if (raw === undefined || raw === '') return DEFAULT_AGENT_URL;
  const parsed = parseBackendUrl(raw);
  if (parsed === null) {
    onInvalid(raw);
    return DEFAULT_AGENT_URL;
  }
  return parsed;
}
