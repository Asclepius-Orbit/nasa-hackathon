import { render, renderHook, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { DataProvider, useData } from './DataContext';
import { MockDataSource } from './mock/MockDataSource';

function Probe(): React.JSX.Element {
  const data = useData();
  return <p>{data instanceof MockDataSource ? 'mock' : 'injected'}</p>;
}

it('uses the mock data source by default', () => {
  render(
    <DataProvider>
      <Probe />
    </DataProvider>,
  );
  expect(screen.getByText('mock')).toBeInTheDocument();
});

it('uses an injected data source', () => {
  const source = new MockDataSource();
  const { result } = renderHook(() => useData(), {
    wrapper: ({ children }) => <DataProvider source={source}>{children}</DataProvider>,
  });
  expect(result.current).toBe(source);
});

it('works without a provider', () => {
  render(<Probe />);
  expect(screen.getByText('mock')).toBeInTheDocument();
});
