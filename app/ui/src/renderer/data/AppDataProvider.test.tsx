import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { ApiProvider } from '../api/ApiContext';
import { FakeAsclepiusApi } from '../test/fake-api';
import { AppDataProvider, shouldUseMockData } from './AppDataProvider';
import { useData } from './DataContext';
import { MockDataSource } from './mock/MockDataSource';
import { RealDataSource } from './real/RealDataSource';

afterEach(() => {
  vi.unstubAllEnvs();
});

function Probe(): React.JSX.Element {
  const data = useData();
  return <p>{data instanceof RealDataSource ? 'real' : data instanceof MockDataSource ? 'mock' : 'injected'}</p>;
}

const renderProbe = (props: Partial<React.ComponentProps<typeof AppDataProvider>> = {}): void => {
  render(
    <ApiProvider api={new FakeAsclepiusApi()}>
      <AppDataProvider {...props}>
        <Probe />
      </AppDataProvider>
    </ApiProvider>,
  );
};

it('serves the backend views by default', () => {
  renderProbe();
  expect(screen.getByText('real')).toBeInTheDocument();
});

it('serves the mock when asked, for the e2e build and for demos', () => {
  renderProbe({ mock: true });
  expect(screen.getByText('mock')).toBeInTheDocument();
});

it('prefers an injected source over both', () => {
  renderProbe({ source: new MockDataSource(), mock: false });
  expect(screen.getByText('mock')).toBeInTheDocument();
});

it('uses the mock in the e2e build mode or with VITE_MOCK_DATA=true, and the backend otherwise', () => {
  expect(shouldUseMockData()).toBe(false);
  vi.stubEnv('MODE', 'e2e');
  expect(shouldUseMockData()).toBe(true);
  vi.stubEnv('MODE', 'production');
  vi.stubEnv('VITE_MOCK_DATA', 'true');
  expect(shouldUseMockData()).toBe(true);
  vi.stubEnv('VITE_MOCK_DATA', 'false');
  expect(shouldUseMockData()).toBe(false);
});
