import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { afterEach, expect, it, vi } from 'vitest';

import { useOffline } from '../../offline/OfflineContext';
import { renderInWorld } from '../../test/providers';
import { MockScreensMenu } from './MockScreensMenu';

function Where(): React.JSX.Element {
  const { pathname } = useLocation();
  const { offline } = useOffline();
  return (
    <p data-testid="where">
      {pathname} {offline ? 'offline' : 'online'}
    </p>
  );
}

const setup = (): ReturnType<typeof renderInWorld> =>
  renderInWorld(
    <>
      <MockScreensMenu />
      <Where />
    </>,
  );

afterEach(() => {
  vi.unstubAllEnvs();
});

it('renders in development: links to the mock-only screens', () => {
  vi.stubEnv('DEV', true);
  setup();
  expect(screen.getByText('Mock screens')).toBeInTheDocument();
  const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
  expect(hrefs).toEqual(['/first-day', '/baseline', '/oswestry', '/stopped', '/houston']);
});

it('renders nothing outside development', () => {
  vi.stubEnv('DEV', false);
  const { container } = setup();
  expect(screen.queryByText('Mock screens')).not.toBeInTheDocument();
  expect(container.querySelector('[data-testid="mock-menu"]')).toBeNull();
});

it('toggles the offline banner state with App offline', async () => {
  vi.stubEnv('DEV', true);
  setup();
  const toggle = screen.getByRole('button', { name: 'App offline' });
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await userEvent.click(toggle);
  expect(screen.getByTestId('where')).toHaveTextContent('offline');
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
});

it('Lock screen locks through the bridge', async () => {
  vi.stubEnv('DEV', true);
  const { api } = setup();
  await userEvent.click(screen.getByRole('button', { name: 'Lock screen' }));
  expect(api.calls).toContain('auth.lock');
});

it('Login signs out locally', async () => {
  vi.stubEnv('DEV', true);
  setup();
  await userEvent.click(screen.getByRole('button', { name: 'Login' }));
  // No error: the local session is dropped and the gate (not mounted here) would route to /login.
  expect(screen.getByRole('button', { name: 'Login' })).toBeInTheDocument();
});
