import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { DataProvider } from './DataContext';
import type { DataSource } from './DataSource';
import { MockDataSource } from './mock/MockDataSource';
import { useLoad } from './useLoad';
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
