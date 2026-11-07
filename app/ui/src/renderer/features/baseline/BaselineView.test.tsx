import { screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
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
