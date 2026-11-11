import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { expect, it } from 'vitest';

import { FakeAsclepiusApi } from './test/fake-api';
import { renderInWorld } from './test/providers';
import { routes } from './router';

function setup(path: string, session: FakeAsclepiusApi['session'] = { crewId: 'AST-01', locked: false }): ReturnType<typeof renderInWorld> {
  const api = new FakeAsclepiusApi();
  api.session = session;
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return renderInWorld(<RouterProvider router={router} />, { api, withRouter: false, signedIn: false });
}

it.each([
  ['/today', 'today'],
  ['/report', 'report'],
  ['/session', 'session'],
  ['/stopped', 'stopped'],
  ['/history', 'history'],
  ['/chat', 'chat'],
  ['/settings', 'settings'],
  ['/profile', 'profile'],
  ['/first-day', 'firstday'],
  ['/baseline', 'baseline'],
  ['/oswestry', 'oswestry'],
  ['/houston', 'houston'],
])('%s shows its screen inside the app frame', async (path, feature) => {
  setup(path);
  expect(await screen.findByTestId(`${feature}-view`)).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Urgent symptom' })).toBeInTheDocument();
});

it('the root and unknown paths land on /today', async () => {
  setup('/');
  expect(await screen.findByTestId('today-view')).toBeInTheDocument();
});

it('unknown paths fall back to /today', async () => {
  setup('/nowhere');
  expect(await screen.findByTestId('today-view')).toBeInTheDocument();
});

it('signed-out users see the login without strip or rail, and Urgent still works', async () => {
  setup('/today', null);
  expect(await screen.findByTestId('login-view')).toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: 'Sections' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Who is using this tablet?' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Urgent symptom, no login needed' }));
  expect(screen.getByRole('dialog', { name: 'What do you have now?' })).toBeInTheDocument();
});

it('a locked session sees only the lock screen, and Urgent works while locked', async () => {
  setup('/history', { crewId: 'AST-01', locked: true });
  expect(await screen.findByTestId('lock-view')).toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: 'Sections' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Urgent symptom, works while locked' }));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
});
