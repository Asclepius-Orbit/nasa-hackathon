import { useEffect, useState } from 'react';

import { useData } from './DataContext';
import type { DataSource } from './DataSource';

export interface Loaded<T> {
  /** `null` while loading or after a failure: never a made-up default, never the last good value. */
  data: T | null;
  error: Error | null;
}

/** How often the views that show live facts (strip, Today, Session...) are read again. */
export const REFRESH_MS = 30_000;

export interface LoadOptions {
  /**
   * Reload every `refreshMs` while the page is visible, and when the window regains focus or becomes visible again.
   * A failed reload replaces the data with the error (the old value is not kept); the next cycle tries again.
   */
  refreshMs?: number;
}

/**
 * Loads one view-model from the DataSource, e.g. `useLoad(selectToday)` with
 * `const selectToday = (d: DataSource) => d.getToday()` defined at module level (it must be a stable function).
 */
export function useLoad<T>(select: (data: DataSource) => Promise<T>, { refreshMs }: LoadOptions = {}): Loaded<T> {
  const source = useData();
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null });
  useEffect(() => {
    let live = true;
    // Only the newest request may write: a slow older answer must not overwrite a newer one.
    let latest = 0;
    const load = (): void => {
      latest += 1;
      const mine = latest;
      select(source).then(
        (data) => {
          if (live && mine === latest) setState({ data, error: null });
        },
        (reason: unknown) => {
          if (live && mine === latest) {
            setState({ data: null, error: reason instanceof Error ? reason : new Error(typeof reason === 'string' ? reason : 'Unknown error') });
          }
        },
      );
    };
    load();
    if (refreshMs === undefined) {
      return () => {
        live = false;
      };
    }
    const visible = (): boolean => document.visibilityState !== 'hidden';
    const timer = setInterval(() => {
      if (visible()) load();
    }, refreshMs);
    const onFocus = (): void => {
      load();
    };
    const onVisibility = (): void => {
      if (visible()) load();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [source, select, refreshMs]);
  return state;
}
