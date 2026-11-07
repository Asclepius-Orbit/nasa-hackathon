import { render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';

import { App } from './App';
import { routes } from './router';
import { FakeAsclepiusApi } from './test/fake-api';

afterEach(() => {
  document.documentElement.removeAttribute('data-text-size');
});

it('mounts every provider: a signed-out launch shows the login with the product name', async () => {
  const router = createMemoryRouter(routes, { initialEntries: ['/'] });
  render(<App api={new FakeAsclepiusApi()} router={router} />);
  expect(await screen.findByRole('heading', { name: 'Who is using this tablet?' })).toBeInTheDocument();
  expect(screen.getByText('Asclepius Orbit, on-board spine monitoring')).toBeInTheDocument();
  expect(screen.getByTestId('login-view')).toBeInTheDocument();
});

it('a signed-in launch lands on Today, with the stored text size applied', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-01', locked: false };
  api.settingsValue = { ...api.settingsValue, textSize: 'large' };
  const router = createMemoryRouter(routes, { initialEntries: ['/'] });
  render(<App api={api} router={router} />);
  expect(await screen.findByTestId('today-view')).toBeInTheDocument();
  await waitFor(() => {
    expect(document.documentElement.dataset.textSize).toBe('large');
  });
});

it('Urgent opens from the signed-in strip', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-01', locked: false };
  const router = createMemoryRouter(routes, { initialEntries: ['/today'] });
  render(<App api={api} router={router} />);
  (await screen.findByRole('button', { name: 'Urgent symptom' })).click();
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
});

it('uses the hash router by default: the URL hash picks the screen', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-01', locked: false };
  window.location.hash = '#/history';
  render(<App api={api} />);
  expect(await screen.findByTestId('history-view')).toBeInTheDocument();
});
