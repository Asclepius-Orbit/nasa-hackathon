import { screen } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { expect, it } from 'vitest';

import { renderInWorld } from '../../test/providers';
import { AppLayout } from './AppLayout';

const tree = (
  <Routes>
    <Route element={<AppLayout />}>
      <Route path="today" element={<h1>Today content</h1>} />
    </Route>
  </Routes>
);

it('frames the routed screen with the strip, the rail and a main landmark', async () => {
  renderInWorld(tree);
  expect(screen.getByRole('main')).toContainElement(screen.getByRole('heading', { name: 'Today content' }));
  expect(screen.getByRole('banner')).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument();
  expect(await screen.findByText('Elena Ruiz')).toBeInTheDocument();
});

it('shows the offline banner only when offline', () => {
  const { unmount } = renderInWorld(tree);
  expect(screen.queryByText('The app cannot reach its data service.')).not.toBeInTheDocument();
  unmount();
  renderInWorld(tree, { initialOffline: true });
  expect(screen.getByText('The app cannot reach its data service.')).toBeInTheDocument();
});
