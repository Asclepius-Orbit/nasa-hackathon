import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { expect, it } from 'vitest';

import { RealDataSource } from '../../data/real/RealDataSource';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { ProfileNotice } from './ProfileNotice';

function Where(): React.JSX.Element {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function setup(api: FakeAsclepiusApi): void {
  renderInWorld(
    <>
      <ProfileNotice />
      <Where />
    </>,
    { api, data: new RealDataSource(api.views, api.reps, api), route: '/today' },
  );
}

it('asks for the profile when none is stored, and the button opens it', async () => {
  const api = new FakeAsclepiusApi();
  api.profileValue = null;
  setup(api);
  expect(await screen.findByText('Add your profile so your assessments can start')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Add profile' }));
  expect(screen.getByTestId('where')).toHaveTextContent('/profile');
});

it('asks for today’s context when the profile exists but the context does not', async () => {
  const api = new FakeAsclepiusApi();
  api.contextValue = null;
  setup(api);
  expect(await screen.findByText('Set today’s mission context')).toBeInTheDocument();
  expect(screen.queryByText('Add your profile so your assessments can start')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Set context' }));
  expect(screen.getByTestId('where')).toHaveTextContent('/profile');
});

it('stays silent when both are stored', async () => {
  const api = new FakeAsclepiusApi();
  setup(api);
  await screen.findByTestId('where');
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(api.calls).toContain('dailyContext.today');
});

it.each([
  ['profile', 'unavailable'],
  ['context', 'unavailable'],
] as const)('shows nothing extra when reading the %s is rejected', async (which, code) => {
  const api = new FakeAsclepiusApi();
  api.readErrors = { [which]: code };
  setup(api);
  await screen.findByTestId('where');
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
