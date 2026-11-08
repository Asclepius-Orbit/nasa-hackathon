import { useMemo } from 'react';

import { useApi } from '../api/ApiContext';
import { DataProvider } from './DataContext';
import type { DataSource } from './DataSource';
import { MockDataSource } from './mock/MockDataSource';
import { RealDataSource } from './real/RealDataSource';

/**
 * True when the app should run on the on-board mock instead of the backend: the e2e build (`electron-vite build --mode e2e`,
 * used by `pnpm e2e`) or a run with `VITE_MOCK_DATA=true` (for example `VITE_MOCK_DATA=true pnpm dev`).
 * Unit tests inject a source instead.
 */
export function shouldUseMockData(): boolean {
  return import.meta.env.MODE === 'e2e' || import.meta.env.VITE_MOCK_DATA === 'true';
}

/** The DataSource of the app: the backend views by default; the mock only when asked. An injected `source` wins. */
export function AppDataProvider({
  source,
  mock = shouldUseMockData(),
  children,
}: {
  source?: DataSource;
  mock?: boolean;
  children: React.ReactNode;
}): React.JSX.Element {
  const api = useApi();
  const value = useMemo(() => source ?? (mock ? new MockDataSource() : new RealDataSource(api.views)), [source, mock, api]);
  return <DataProvider source={value}>{children}</DataProvider>;
}
