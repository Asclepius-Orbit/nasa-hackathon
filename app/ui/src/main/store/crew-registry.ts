/**
 * Keeps the backend's crew registry (`PUT /ui/crew/{id}`) in step with the encrypted crew store. The store stays the
 * source of truth for the member list, the `AST-NN` ids, the names and the PINs; the backend only learns the id.
 * The request carries no body: no name and no PIN hash ever leaves the main process.
 *
 * Registering is idempotent and best effort. A backend that is down or answers badly never fails the crew operation:
 * the id stays pending and is tried again on the next `list` or `create` (and so at startup, when the store is first
 * listed). Ids confirmed in this run are not sent again.
 */
import { type AuthHeaders, authHeaders } from '../views/backend-auth';
import type { CrewStore } from './crew-store';

const DEFAULT_TIMEOUT_MS = 5_000;

/** The part of `fetch` used here, so tests can pass a hand-written fake. */
export type RegisterFetch = (
  url: string,
  init: { method: 'PUT'; headers: AuthHeaders; redirect: 'error'; signal: AbortSignal },
) => Promise<Pick<Response, 'ok'>>;

export interface CrewRegistryOptions {
  /** Origin of the backend (already checked to be loopback). */
  baseUrl: string;
  fetch?: RegisterFetch;
  timeoutMs?: number;
  logError?: (message: string) => void;
  /** Shared token of the /ui API: registering needs no unlocked astronaut. */
  token?: string | null;
}

export interface CrewRegistry {
  /** Registers the ids not yet confirmed in this run; resolves once every attempt is over, never rejects. */
  register(ids: readonly string[]): Promise<void>;
}

export function createCrewRegistry({
  baseUrl,
  fetch: doFetch = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  logError = () => undefined,
  token = null,
}: CrewRegistryOptions): CrewRegistry {
  const confirmed = new Set<string>();

  async function registerOne(id: string): Promise<void> {
    try {
      const response = await doFetch(`${baseUrl}/ui/crew/${encodeURIComponent(id)}`, {
        method: 'PUT',
        headers: authHeaders(token),
        redirect: 'error',
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (response.ok) confirmed.add(id);
      else logError(`crew registry: ${id} not registered (backend refused it)`);
    } catch {
      logError(`crew registry: ${id} not registered (backend unavailable)`);
    }
  }

  return {
    register: async (ids) => {
      await Promise.all(ids.filter((id) => !confirmed.has(id)).map(registerOne));
    },
  };
}

/**
 * The crew store with registration on the side: `list` and a successful `create` start registering in the background,
 * so neither waits for the backend nor fails because of it.
 */
export function withCrewRegistry(store: CrewStore, registry: CrewRegistry): CrewStore {
  return {
    ...store,
    list: async () => {
      const members = await store.list();
      if (members !== null) void registry.register(members.map((m) => m.id));
      return members;
    },
    create: async (input) => {
      const result = await store.create(input);
      if (result.ok) void registry.register([result.member.id]);
      return result;
    },
  };
}
