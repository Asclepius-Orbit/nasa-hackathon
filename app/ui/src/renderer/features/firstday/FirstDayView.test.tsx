import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import { renderInWorld } from '../../test/providers';
import { FirstDayView } from './FirstDayView';

function Where(): React.JSX.Element {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

it('greets the crew member and says explicitly that nothing is assessed', async () => {
  renderInWorld(<FirstDayView />, { route: '/first-day' });
  expect(await screen.findByRole('heading', { level: 1, name: 'Welcome, Elena' })).toBeInTheDocument();
  expect(screen.getByText(/Nothing here means “all fine”/)).toBeInTheDocument();
  expect(screen.getByText('Not assessed yet')).toBeInTheDocument();
  expect(screen.queryByText('Green')).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 2, name: 'Three things before the first assessment' })).toBeInTheDocument();
});

it('lists the three tasks; the baseline one navigates, the ones without a screen are disabled', async () => {
  renderInWorld(
    <>
      <FirstDayView />
      <Where />
    </>,
    { route: '/first-day' },
  );
  const profile = await screen.findByRole('button', { name: 'Open profile' });
  expect(profile).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Pair harness' })).toBeDisabled();
  expect(screen.getByText('No harness seen yet.')).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  const plan = screen.getByRole('button', { name: 'See the plan' });
  expect(plan).toBeEnabled();
  await userEvent.click(plan);
  expect(screen.getByTestId('where')).toHaveTextContent('/baseline');
});

it('says so when the checklist cannot be loaded', async () => {
  class Failing extends MockDataSource {
    override getFirstDay(): ReturnType<MockDataSource['getFirstDay']> {
      return Promise.reject(new Error('down'));
    }
  }
  renderInWorld(<FirstDayView />, { data: new Failing(), route: '/first-day' });
  expect(await screen.findByRole('alert')).toHaveTextContent('The first-day checklist could not be loaded.');
  expect(within(screen.getByTestId('firstday-view')).queryByRole('list')).not.toBeInTheDocument();
});
