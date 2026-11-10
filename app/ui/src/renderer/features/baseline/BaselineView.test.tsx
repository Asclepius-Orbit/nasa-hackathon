import { screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import { RealDataSource } from '../../data/real/RealDataSource';
import type { BaselineView as BaselinePayload } from '../../../shared/api';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { BaselineView } from './BaselineView';

it('shows the preflight reference: progress, the three sessions and the reference version', async () => {
  renderInWorld(<BaselineView />, { route: '/baseline' });
  expect(await screen.findByRole('heading', { level: 1, name: 'Your preflight reference' })).toBeInTheDocument();
  expect(screen.getByText(/built from sessions in 1 g, before launch/)).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 2, name: '2 of 3 sessions recorded' })).toBeInTheDocument();
  const bar = screen.getByRole('progressbar', { name: 'Preflight sessions recorded' });
  expect(bar).toHaveAttribute('aria-valuenow', '2');
  expect(bar).toHaveAttribute('aria-valuemax', '3');
  const list = screen.getByRole('list', { name: 'Preflight sessions' });
  const items = within(list).getAllByRole('listitem');
  expect(items.map((i) => i.textContent)).toEqual([
    'Session 1L-30, standing lat pulldown, complete',
    'Session 2L-23, standing lat pulldown, complete',
    'Session 3Planned for L-16',
  ]);
  expect(screen.getByText('Version 2, built from 2 sessions. Assessments say “low confidence” until the third.')).toBeInTheDocument();
  expect(screen.getByText(/earlier versions are kept/)).toBeInTheDocument();
});

it('shows an explicit message when the reference cannot be loaded', async () => {
  class Failing extends MockDataSource {
    override getBaseline(): ReturnType<MockDataSource['getBaseline']> {
      return Promise.reject(new Error('down'));
    }
  }
  renderInWorld(<BaselineView />, { data: new Failing(), route: '/baseline' });
  expect(await screen.findByRole('alert')).toHaveTextContent('The preflight reference could not be loaded.');
});

it('uses the singular when the reference is built from one session', async () => {
  class One extends MockDataSource {
    override getBaseline(): ReturnType<MockDataSource['getBaseline']> {
      return super.getBaseline().then((b) => ({ ...b, recorded: 1, builtFromSessions: 1, referenceVersion: 1 }));
    }
  }
  renderInWorld(<BaselineView />, { data: new One(), route: '/baseline' });
  expect(await screen.findByText(/Version 1, built from 1 session\./)).toBeInTheDocument();
});

const realBaseline = (view: BaselinePayload | null): void => {
  const api = new FakeAsclepiusApi();
  if (view) api.viewData.baseline = view;
  renderInWorld(<BaselineView />, { api, data: new RealDataSource(api.views, api.reps), route: '/baseline' });
};

it('shows the backend example with the session dates', async () => {
  realBaseline(example<BaselinePayload>('ui-baseline'));
  const list = await screen.findByRole('list', { name: 'Preflight sessions' });
  expect(within(list).getAllByRole('listitem').map((i) => i.textContent)).toEqual([
    'Session 1L-30 (2026-10-16), standing lat pulldown, complete',
    'Session 2L-23 (2026-10-23), standing lat pulldown, complete',
    'Session 3Planned for L-16 (2026-10-30)',
  ]);
});

it('says there is no reference yet when none was built, with nothing recorded', async () => {
  realBaseline(example<BaselinePayload>('ui-baseline.empty'));
  expect(await screen.findByRole('heading', { level: 2, name: '0 of 3 sessions recorded' })).toBeInTheDocument();
  expect(screen.getByText(/No reference built yet/)).toBeInTheDocument();
  expect(screen.queryByText(/Version/)).not.toBeInTheDocument();
  expect(screen.getByText('Planned for L-30')).toBeInTheDocument();
});

it('says there is no connection to the monitor when the backend is down', async () => {
  realBaseline(null);
  expect(await screen.findByTestId('no-monitor')).toBeInTheDocument();
});
