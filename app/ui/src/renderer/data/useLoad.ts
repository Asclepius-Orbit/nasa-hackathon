import { useEffect, useState } from 'react';

import { useData } from './DataContext';
import type { DataSource } from './DataSource';

export interface Loaded<T> {
  /** `null` while loading or after a failure: never a made-up default. */
  data: T | null;
  error: Error | null;
}

/**
 * Loads one view-model from the DataSource, e.g. `useLoad(selectToday)` with
 * `const selectToday = (d: DataSource) => d.getToday()` defined at module level (it must be a stable function).
 */
export function useLoad<T>(select: (data: DataSource) => Promise<T>): Loaded<T> {
  const source = useData();
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null });
  useEffect(() => {
    let live = true;
    select(source).then(
      (data) => {
        if (live) setState({ data, error: null });
      },
      (reason: unknown) => {
        if (live) setState({ data: null, error: reason instanceof Error ? reason : new Error(typeof reason === 'string' ? reason : 'Unknown error') });
      },
    );
    return () => {
      live = false;
    };
  }, [source, select]);
  return state;
}
