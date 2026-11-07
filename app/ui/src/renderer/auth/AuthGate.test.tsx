import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { expect, it } from 'vitest';

import { ApiProvider } from '../api/ApiContext';
import { FakeAsclepiusApi } from '../test/fake-api';
import { AuthGate } from './AuthGate';
import { AuthProvider } from './AuthProvider';

function setup(session: FakeAsclepiusApi['session'], path: string): void {
  const api = new FakeAsclepiusApi();
  api.session = session;
  const router = createMemoryRouter(
    [
      {
        element: <AuthGate />,
        children: [
          { path: '/login', element: <p>login page</p> },
          { path: '/lock', element: <p>lock page</p> },
          { path: '/today', element: <p>today page</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  render(
    <ApiProvider api={api}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </ApiProvider>,
  );
}

it('sends a signed-out visitor to /login', async () => {
  setup(null, '/today');
  expect(await screen.findByText('login page')).toBeInTheDocument();
});

it('sends a locked session to /lock', async () => {
  setup({ crewId: 'AST-01', locked: true }, '/today');
  expect(await screen.findByText('lock page')).toBeInTheDocument();
});

it('sends a signed-in user away from /login to /today', async () => {
  setup({ crewId: 'AST-01', locked: false }, '/login');
  expect(await screen.findByText('today page')).toBeInTheDocument();
});

it('shows nothing of the app while the session is still loading', async () => {
  setup(null, '/today');
  expect(screen.queryByText('today page')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Loading');
  await screen.findByText('login page');
});
