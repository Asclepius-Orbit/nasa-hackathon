import { useState } from 'react';
import { RouterProvider } from 'react-router';

import type { AsclepiusApi } from '../shared/api';
import { ApiProvider } from './api/ApiContext';
import { AuthProvider } from './auth/AuthProvider';
import { ClockProvider } from './clock/Clock';
import { DataProvider } from './data/DataContext';
import { UrgentProvider } from './features/urgent/UrgentContext';
import { UrgentDialog } from './features/urgent/UrgentDialog';
import { OfflineProvider } from './offline/OfflineContext';
import { createAppRouter } from './router';
import { SettingsProvider } from './settings/SettingsProvider';

export interface AppProps {
  /** Defaults to `window.asclepius` (the preload bridge). */
  api?: AsclepiusApi;
  /** Defaults to the hash router. */
  router?: ReturnType<typeof createAppRouter>;
}

/** Providers, then the router. Order matters: Settings and Auth need Api; Urgent sits above the router so login and lock can use it. */
export function App({ api, router }: AppProps): React.JSX.Element {
  const [appRouter] = useState(() => router ?? createAppRouter());
  return (
    <ApiProvider {...(api ? { api } : {})}>
      <ClockProvider>
        <SettingsProvider>
          <AuthProvider>
            <DataProvider>
              <OfflineProvider>
                <UrgentProvider>
                  <RouterProvider router={appRouter} />
                  <UrgentDialog />
                </UrgentProvider>
              </OfflineProvider>
            </DataProvider>
          </AuthProvider>
        </SettingsProvider>
      </ClockProvider>
    </ApiProvider>
  );
}
