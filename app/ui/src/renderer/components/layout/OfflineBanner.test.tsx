import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { DataProvider } from '../../data/DataContext';
import { MockDataSource } from '../../data/mock/MockDataSource';
import { OfflineProvider } from '../../offline/OfflineContext';
import { OfflineBanner } from './OfflineBanner';

const setup = (offline: boolean, onRetry = vi.fn()): ReturnType<typeof vi.fn> => {
  render(
    <DataProvider source={new MockDataSource()}>
      <OfflineProvider initialOffline={offline} onRetry={onRetry}>
        <OfflineBanner />
      </OfflineProvider>
    </DataProvider>,
  );
  return onRetry;
};

it('is absent while the data service is reachable', () => {
  setup(false);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('says what is happening, what is kept, and when contact was last made', async () => {
  setup(true);
  const banner = screen.getByRole('status');
  expect(banner).toHaveTextContent('The app cannot reach its data service.');
  expect(banner).toHaveTextContent('What you enter is kept on this tablet and saved when it reconnects.');
  expect(banner).toHaveTextContent('Urgent symptom still works.');
  expect(await screen.findByText(/Last contact 4 min ago \(simulated link\)\./)).toBeInTheDocument();
});

it('Try again runs the retry hook', async () => {
  const onRetry = setup(true);
  await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(onRetry).toHaveBeenCalledOnce();
});
