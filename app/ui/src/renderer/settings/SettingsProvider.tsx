import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import type { Settings } from '../../shared/api';
import { useApi } from '../api/ApiContext';
import { formatLength, formatTime } from './format';

export const DEFAULT_SETTINGS: Settings = {
  lengthUnit: 'mm',
  timeFormat: 'gmt',
  textSize: 'large',
  voiceCommands: true,
  footPedal: false,
};

export interface SettingsApi {
  settings: Settings;
  /** False until the stored settings have been read; defaults apply meanwhile. */
  loaded: boolean;
  /** Saves a change through the bridge, then applies it. */
  update: (patch: Partial<Settings>) => Promise<void>;
  /** Signed length in the chosen unit, e.g. `+2.1 mm`. */
  formatLength: (mm: number) => string;
  /** Clock time in the chosen format (GMT, or GMT plus mission elapsed time). */
  formatTime: (date: Date, missionStart: Date | null) => string;
}

const SettingsContext = createContext<SettingsApi | null>(null);

/** Loads settings from main and applies them: `data-text-size` on the root element scales the root font size. */
export function SettingsProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const api = useApi();
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let live = true;
    api.settings
      .get()
      .then((stored) => {
        if (live) setSettings(stored);
      })
      .catch(() => {
        // Keep the defaults; the screens stay usable.
      })
      .finally(() => {
        if (live) setLoaded(true);
      });
    return () => {
      live = false;
    };
  }, [api]);

  useEffect(() => {
    document.documentElement.dataset.textSize = settings.textSize;
  }, [settings.textSize]);

  const update = useCallback(
    async (patch: Partial<Settings>) => {
      setSettings(await api.settings.update(patch));
    },
    [api],
  );

  const value = useMemo<SettingsApi>(
    () => ({
      settings,
      loaded,
      update,
      formatLength: (mm) => formatLength(mm, settings.lengthUnit),
      formatTime: (date, missionStart) => formatTime(date, settings.timeFormat, missionStart),
    }),
    [settings, loaded, update],
  );
  return <SettingsContext value={value}>{children}</SettingsContext>;
}

export function useSettings(): SettingsApi {
  const value = useContext(SettingsContext);
  if (value === null) throw new Error('useSettings must be used inside SettingsProvider');
  return value;
}
