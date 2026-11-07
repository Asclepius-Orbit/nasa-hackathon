import { createContext, useContext, useMemo, useState } from 'react';

export interface OfflineApi {
  /** True while the app cannot reach its data service (shows the banner). */
  offline: boolean;
  setOffline: (offline: boolean) => void;
  /** "Try again": runs the `onRetry` hook given to the provider. */
  retry: () => void;
}

const OfflineContext = createContext<OfflineApi | null>(null);

/** Holds the offline flag. The real reachability check plugs in through `onRetry`/`setOffline` later. */
export function OfflineProvider({
  initialOffline = false,
  onRetry,
  children,
}: {
  initialOffline?: boolean;
  onRetry?: () => void;
  children: React.ReactNode;
}): React.JSX.Element {
  const [offline, setOffline] = useState(initialOffline);
  const value = useMemo<OfflineApi>(
    () => ({
      offline,
      setOffline,
      retry: () => {
        onRetry?.();
      },
    }),
    [offline, onRetry],
  );
  return <OfflineContext value={value}>{children}</OfflineContext>;
}

export function useOffline(): OfflineApi {
  const value = useContext(OfflineContext);
  if (value === null) throw new Error('useOffline must be used inside OfflineProvider');
  return value;
}
