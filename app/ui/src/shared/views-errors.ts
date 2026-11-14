/**
 * Normalizes the failure of a view call at the bridge. `ipcRenderer.invoke` rejects with the message
 * "Error invoking remote method '<channel>': Error: <code>", while the renderer reads exactly the code
 * (`ViewsErrorCode`). Done once, in the preload, so no screen parses Electron's wording.
 */
import type { ViewsErrorCode } from './api';

const CODES: readonly ViewsErrorCode[] = ['locked', 'not_found', 'unavailable'];
const WRAPPED = /^Error invoking remote method '[^']*': \w*Error: ([a-z_]+)$/u;

function codeOf(message: string): ViewsErrorCode | null {
  const code = WRAPPED.exec(message)?.[1] ?? message;
  return CODES.find((known) => known === code) ?? null;
}

/** An `Error` whose message is exactly a `ViewsErrorCode`; anything unrecognized is `unavailable`, and its text is dropped. */
export function normalizeViewsError(reason: unknown): Error {
  const code = reason instanceof Error ? codeOf(reason.message) : null;
  return new Error(code ?? 'unavailable');
}

/** Runs one bridge call and rejects, if it fails, with the normalized error. */
export async function viewsCall<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (reason) {
    throw normalizeViewsError(reason);
  }
}
