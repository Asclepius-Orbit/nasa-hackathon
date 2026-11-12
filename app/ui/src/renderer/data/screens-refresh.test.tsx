import { act, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BaselineView, FirstDayResult, HistoryView, SessionView, StripView, TodayView } from '../../shared/api';
import { OfflineBanner } from '../components/layout/OfflineBanner';
import { Rail } from '../components/layout/Rail';
import { Strip } from '../components/layout/Strip';
import { BaselineView as Baseline } from '../features/baseline/BaselineView';
import { FirstDayView } from '../features/firstday/FirstDayView';
import { HistoryView as History } from '../features/history/HistoryView';
import { SessionView as Session } from '../features/session/SessionView';
import { TodayView as Today } from '../features/today/TodayView';
import { example } from '../test/contract-examples';
import { FakeAsclepiusApi } from '../test/fake-api';
import { renderInWorld } from '../test/providers';
import { RealDataSource } from './real/RealDataSource';

const SESSION_ID = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';

function feed(): FakeAsclepiusApi {
  const api = new FakeAsclepiusApi();
  api.viewData = {
    strip: example<StripView>('ui-strip'),
    today: example<TodayView>('ui-today.nodata'),
    history: example<HistoryView>('ui-history'),
    baseline: example<BaselineView>('ui-baseline'),
    firstDay: { ...example<FirstDayResult>('ui-first-day'), crewFirstName: 'Elena' },
    sessions: { [SESSION_ID]: example<SessionView>('ui-session') },
  };
  return api;
}

const screens: [string, () => React.ReactElement, `views.${string}`, 'strip' | 'today' | 'history' | 'baseline' | 'firstDay'][] = [
  ['the strip', () => <Strip />, 'views.strip', 'strip'],
  ['the rail badge', () => <Rail />, 'views.strip', 'strip'],
  ['the offline banner', () => <OfflineBanner />, 'views.strip', 'strip'],
  ['Today', () => <Today />, 'views.today', 'today'],
  ['Session', () => <Session />, 'views.today', 'today'],
  ['History', () => <History />, 'views.history', 'history'],
  ['Baseline', () => <Baseline />, 'views.baseline', 'baseline'],
  ['First day', () => <FirstDayView />, 'views.firstDay', 'firstDay'],
];

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('screens that show live facts reload by themselves', () => {
  const count = (api: FakeAsclepiusApi, call: string): number => api.calls.filter((c) => c === call).length;
  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

  it.each(screens)('%s reads its view again after 30 s and when the window regains focus', async (_name, element, call) => {
    const api = feed();
    renderInWorld(element(), { api, data: new RealDataSource(api.views, api.reps, api), initialOffline: true });
    await tick(0);
    expect(count(api, call)).toBe(1);
    await tick(30_000);
    expect(count(api, call)).toBe(2);
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await tick(0);
    expect(count(api, call)).toBe(3);
  });

  it.each(screens.filter(([name]) => name !== 'the rail badge' && name !== 'the offline banner'))(
    '%s shows no connection (and nothing old) after a failed reload',
    async (_name, element, _call, key) => {
      const api = feed();
      renderInWorld(element(), { api, data: new RealDataSource(api.views, api.reps, api) });
      await tick(0);
      api.viewErrors = { [key]: 'unavailable' };
      await tick(30_000);
      expect(screen.getAllByText(/No connection to the monitor/).length).toBeGreaterThan(0);
      api.viewErrors = {};
      await tick(30_000);
      expect(screen.queryByText(/No connection to the monitor/)).not.toBeInTheDocument();
    },
  );
});
