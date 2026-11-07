import { createContext, useContext } from 'react';

import type { AsclepiusApi } from '../../shared/api';

const ApiContext = createContext<AsclepiusApi | null>(null);

/** Provides the bridge to main. Tests pass a fake; the app passes nothing and gets `window.asclepius`. */
export function ApiProvider({ api, children }: { api?: AsclepiusApi; children: React.ReactNode }): React.JSX.Element {
  return <ApiContext value={api ?? window.asclepius}>{children}</ApiContext>;
}

/** The bridge to main (the only way the renderer talks to the main process). */
export function useApi(): AsclepiusApi {
  return useContext(ApiContext) ?? window.asclepius;
}
