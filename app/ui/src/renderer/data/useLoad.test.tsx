import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DataProvider } from './DataContext';
import type { DataSource } from './DataSource';
import { MockDataSource } from './mock/MockDataSource';
import { REFRESH_MS, useLoad } from './useLoad';
import type { TodayData } from './types';

const selectToday = (data: DataSource): Promise<TodayData> => data.getToday();

function Probe(): React.JSX.Element {
  const { data, error } = useLoad(selectToday);
  if (error) return <p>failed: {error.message}</p>;
  return <p>{data === null ? 'loading' : `level ${data.level}`}</p>;
}

it('is null while loading, then the loaded view-model', async () => {
  render(
    <DataProvider source={new MockDataSource()}>
      <Probe />
    </DataProvider>,
  );
  expect(screen.getByText('loading')).toBeInTheDocument();
  expect(await screen.findByText('level amber')).toBeInTheDocument();
});

it('reports a failure instead of showing stale or default data', async () => {
  class Broken extends MockDataSource {
    override getToday(): Promise<TodayData> {
      return Promise.reject(new Error('no link'));
    }
  }
  render(
    <DataProvider source={new Broken()}>
      <Probe />
    </DataProvider>,
  );
  expect(await screen.findByText('failed: no link')).toBeInTheDocument();
});

it('wraps a non-error rejection', async () => {
  class Broken extends MockDataSource {
    override getToday(): Promise<TodayData> {
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      return Promise.reject('plain string');
    }
  }
  render(
    <DataProvider source={new Broken()}>
      <Probe />
    </DataProvider>,
  );
  expect(await screen.findByText('failed: plain string')).toBeInTheDocument();
});

describe('refreshMs', () => {
  /** A source whose `getToday` answers from a queue the test controls (a value, an error, or a promise to settle later). */
  class Scripted extends MockDataSource {
    calls = 0;
    answers: (TodayData['level'] | Error | Promise<TodayData['level']>)[] = [];
    override async getToday(): Promise<TodayData> {
      this.calls += 1;
      const answer = this.answers.shift() ?? 'green';
      if (answer instanceof Error) throw answer;
      return { ...(await super.getToday()), level: await answer };
    }
  }

  function Refreshing({ refreshMs }: { refreshMs?: number }): React.JSX.Element {
    const { data, error } = useLoad(selectToday, refreshMs === undefined ? undefined : { refreshMs });
    if (error) return <p>failed: {error.message}</p>;
    return <p>{data === null ? 'loading' : `level ${data.level}`}</p>;
  }

  const show = (source: Scripted, refreshMs?: number) =>
    render(
      <DataProvider source={source}>
        <Refreshing {...(refreshMs === undefined ? {} : { refreshMs })} />
      </DataProvider>,
    );
  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
  const setVisibility = (state: 'visible' | 'hidden') => vi.spyOn(document, 'visibilityState', 'get').mockReturnValue(state);

  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('defaults the refresh period to 30 s', () => {
    expect(REFRESH_MS).toBe(30_000);
  });

  it('reloads on every period and shows the new value', async () => {
    const source = new Scripted();
    source.answers = ['green', 'amber', 'red'];
    show(source, 30_000);
    await tick(0);
    expect(screen.getByText('level green')).toBeInTheDocument();
    await tick(29_999);
    expect(source.calls).toBe(1);
    await tick(1);
    expect(screen.getByText('level amber')).toBeInTheDocument();
    await tick(30_000);
    expect(screen.getByText('level red')).toBeInTheDocument();
    expect(source.calls).toBe(3);
  });

  it('loads once and never again without refreshMs', async () => {
    const source = new Scripted();
    show(source);
    await tick(10 * 60_000);
    expect(source.calls).toBe(1);
  });

  it('shows the failure with no old data, then recovers on the next cycle', async () => {
    const source = new Scripted();
    source.answers = ['green', new Error('unavailable'), 'amber'];
    show(source, 30_000);
    await tick(0);
    expect(screen.getByText('level green')).toBeInTheDocument();
    await tick(30_000);
    expect(screen.getByText('failed: unavailable')).toBeInTheDocument();
    expect(screen.queryByText('level green')).not.toBeInTheDocument();
    await tick(30_000);
    expect(screen.getByText('level amber')).toBeInTheDocument();
  });

  it('reloads when the window regains focus', async () => {
    const source = new Scripted();
    source.answers = ['green', 'red'];
    show(source, 30_000);
    await tick(0);
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await tick(0);
    expect(screen.getByText('level red')).toBeInTheDocument();
  });

  it('reloads when the page becomes visible again, and does not when it hides', async () => {
    const source = new Scripted();
    source.answers = ['green', 'red'];
    show(source, 30_000);
    await tick(0);
    setVisibility('hidden');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await tick(0);
    expect(source.calls).toBe(1);
    setVisibility('visible');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await tick(0);
    expect(source.calls).toBe(2);
    expect(screen.getByText('level red')).toBeInTheDocument();
  });

  it('does not poll while the page is hidden', async () => {
    const source = new Scripted();
    show(source, 30_000);
    await tick(0);
    setVisibility('hidden');
    await tick(5 * 30_000);
    expect(source.calls).toBe(1);
  });

  it('keeps the newest answer when an older request settles last', async () => {
    const source = new Scripted();
    let release: (level: TodayData['level']) => void = () => undefined;
    source.answers = [
      new Promise<TodayData['level']>((resolve) => {
        release = resolve;
      }),
      'red',
    ];
    show(source, 30_000);
    await tick(30_000);
    expect(screen.getByText('level red')).toBeInTheDocument();
    release('green');
    await tick(0);
    expect(screen.getByText('level red')).toBeInTheDocument();
  });

  it('stops the timer and the listeners on unmount', async () => {
    const source = new Scripted();
    const { unmount } = show(source, 30_000);
    await tick(0);
    unmount();
    window.dispatchEvent(new Event('focus'));
    await tick(5 * 30_000);
    expect(source.calls).toBe(1);
  });
});
