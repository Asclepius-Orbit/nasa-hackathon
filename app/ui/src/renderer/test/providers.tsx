import { render, type RenderResult } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { ApiProvider } from '../api/ApiContext';
import { AuthProvider } from '../auth/AuthProvider';
import { ClockProvider, type Clock } from '../clock/Clock';
import type { DataSource } from '../data/DataSource';
import { DataProvider } from '../data/DataContext';
import { MockDataSource } from '../data/mock/MockDataSource';
import { UrgentProvider } from '../features/urgent/UrgentContext';
import { UrgentDialog } from '../features/urgent/UrgentDialog';
import { OfflineProvider } from '../offline/OfflineContext';
import { SettingsProvider } from '../settings/SettingsProvider';
import { FakeAsclepiusApi } from './fake-api';
import { FakeClock } from './fake-clock';
import { FakeScheduler } from './fake-scheduler';

export interface TestWorld {
  api: FakeAsclepiusApi;
  clock: FakeClock;
  scheduler: FakeScheduler;
  data: DataSource;
}

export interface WorldOptions {
  api?: FakeAsclepiusApi;
  clock?: Clock & FakeClock;
  data?: DataSource;
  route?: string;
  initialOffline?: boolean;
  /** Start signed in as AST-01 (default true). */
  signedIn?: boolean;
  /** Set false when `ui` brings its own router (default true: wraps `ui` in a MemoryRouter at `route`). */
  withRouter?: boolean;
}

/** Renders `ui` inside every provider the app has, with fakes at the boundaries (bridge, clock, scheduler). */
export function renderInWorld(ui: React.ReactNode, options: WorldOptions = {}): RenderResult & TestWorld {
  const api = options.api ?? new FakeAsclepiusApi();
  if (options.signedIn !== false && api.session === null) api.session = { crewId: 'AST-01', locked: false };
  const clock = options.clock ?? new FakeClock('2026-11-05T13:40:00Z');
  const scheduler = new FakeScheduler();
  const data = options.data ?? new MockDataSource({ now: () => clock.now(), scheduler });
  const result = render(
    <ApiProvider api={api}>
      <ClockProvider clock={clock}>
        <SettingsProvider>
          <AuthProvider>
            <DataProvider source={data}>
              <OfflineProvider initialOffline={options.initialOffline ?? false}>
                <UrgentProvider>
                  {options.withRouter === false ? ui : <MemoryRouter initialEntries={[options.route ?? '/today']}>{ui}</MemoryRouter>}
                  <UrgentDialog />
                </UrgentProvider>
              </OfflineProvider>
            </DataProvider>
          </AuthProvider>
        </SettingsProvider>
      </ClockProvider>
    </ApiProvider>,
  );
  return { ...result, api, clock, scheduler, data };
}
