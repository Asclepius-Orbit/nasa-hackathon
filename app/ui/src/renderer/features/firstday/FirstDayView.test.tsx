import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import { RealDataSource } from '../../data/real/RealDataSource';
import type { FirstDayResult } from '../../../shared/api';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
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
  expect(screen.getByRole('heading', { level: 2, name: 'Before the first assessment' })).toBeInTheDocument();
});

it('lists the tasks from their codes; each opens its screen', async () => {
  renderInWorld(
    <>
      <FirstDayView />
      <Where />
    </>,
    { route: '/first-day' },
  );
  const plan = await screen.findByRole('button', { name: 'See the plan' });
  expect(screen.getByText('Record your preflight reference')).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(2);
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

it('shows the backend checklist, and nothing under it when there is nothing left to do', async () => {
  const api = new FakeAsclepiusApi();
  api.viewData.firstDay = { ...example<FirstDayResult>('ui-first-day'), todo: [], crewFirstName: 'Marc' };
  renderInWorld(<FirstDayView />, { api, data: new RealDataSource(api.views), route: '/first-day' });
  expect(await screen.findByRole('heading', { level: 1, name: 'Welcome, Marc' })).toBeInTheDocument();
  expect(screen.queryAllByRole('listitem')).toHaveLength(0);
});

it('says there is no connection to the monitor when the backend is down', async () => {
  const api = new FakeAsclepiusApi();
  renderInWorld(<FirstDayView />, { api, data: new RealDataSource(api.views), route: '/first-day' });
  expect(await screen.findByTestId('no-monitor')).toBeInTheDocument();
});
