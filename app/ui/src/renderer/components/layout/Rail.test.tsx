import { screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import type { StripData } from '../../data/types';
import { renderInWorld } from '../../test/providers';
import { Rail } from './Rail';

it('is a named navigation with the six tabs in order', () => {
  renderInWorld(<Rail />);
  const nav = screen.getByRole('navigation', { name: 'Sections' });
  expect(nav).toBeInTheDocument();
  const names = screen.getAllByRole('link').slice(0, 6).map((a) => a.textContent.replace(/\s*1$/, ''));
  expect(names).toEqual(['Today', 'Report', 'Session', 'History', 'Ask', 'Settings']);
});

it('points each tab at its route and marks the current one', () => {
  renderInWorld(<Rail />, { route: '/session' });
  expect(screen.getByRole('link', { name: 'Session' })).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('link', { name: 'Session' })).toHaveAttribute('href', '/session');
  expect(screen.getByRole('link', { name: /^Today/ })).not.toHaveAttribute('aria-current');
});

it('shows the Report badge from the data source', async () => {
  renderInWorld(<Rail />);
  expect(await screen.findByLabelText('1 due')).toHaveTextContent('1');
});

it('hides the badge when nothing is due', async () => {
  class NoneDue extends MockDataSource {
    override getStrip(): Promise<StripData> {
      return super.getStrip().then((s) => ({ ...s, reportsDue: 0 }));
    }
  }
  renderInWorld(<Rail />, { data: new NoneDue() });
  await screen.findByRole('link', { name: 'Report' });
  expect(screen.queryByLabelText(/due/)).not.toBeInTheDocument();
});
