import { createContext, useContext, useMemo } from 'react';

import type { DataSource } from './DataSource';
import { MockDataSource } from './mock/MockDataSource';

const DataContext = createContext<DataSource | null>(null);

/** Provides the DataSource. Defaults to the mock; tests and the future real source pass `source`. */
export function DataProvider({ source, children }: { source?: DataSource; children: React.ReactNode }): React.JSX.Element {
  const value = useMemo(() => source ?? new MockDataSource(), [source]);
  return <DataContext value={value}>{children}</DataContext>;
}

const fallback = new MockDataSource();

/** The DataSource for health and mission data (the only place screens read it from). */
export function useData(): DataSource {
  return useContext(DataContext) ?? fallback;
}
