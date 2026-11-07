import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { useAuth } from '../../auth/AuthProvider';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { MockSkipLogin } from './MockSkipLogin';

function Status(): React.JSX.Element {
  const { status, crewId } = useAuth();
  return (
    <p data-testid="status">
      {status} {crewId}
    </p>
  );
}

afterEach(() => {
  vi.unstubAllEnvs();
});

it('in development, signs in as the first crew member without a PIN', async () => {
  vi.stubEnv('DEV', true);
  const api = new FakeAsclepiusApi();
  renderInWorld(
    <>
      <MockSkipLogin />
      <Status />
    </>,
    { api, signedIn: false },
  );
  await waitFor(() => {
    expect(screen.getByTestId('status')).toHaveTextContent('signed-out');
  });
  await userEvent.click(screen.getByRole('button', { name: 'Mock: skip login or lock' }));
  await waitFor(() => {
    expect(screen.getByTestId('status')).toHaveTextContent('signed-in AST-01');
  });
});

it('does nothing when there is no crew member to sign in as', async () => {
  vi.stubEnv('DEV', true);
  const api = new FakeAsclepiusApi();
  api.crewMembers = [];
  renderInWorld(
    <>
      <MockSkipLogin />
      <Status />
    </>,
    { api, signedIn: false },
  );
  await waitFor(() => {
    expect(screen.getByTestId('status')).toHaveTextContent('signed-out');
  });
  await userEvent.click(screen.getByRole('button', { name: 'Mock: skip login or lock' }));
  expect(screen.getByTestId('status')).toHaveTextContent('signed-out');
});

it('is absent in production builds', () => {
  vi.stubEnv('DEV', false);
  renderInWorld(<MockSkipLogin />, { signedIn: false });
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
