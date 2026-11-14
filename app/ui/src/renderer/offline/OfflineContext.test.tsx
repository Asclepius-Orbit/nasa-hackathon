import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { OfflineProvider, useOffline } from './OfflineContext';

function Probe(): React.JSX.Element {
  const { offline, setOffline, retry } = useOffline();
  return (
    <div>
      <p>{offline ? 'offline' : 'online'}</p>
      <button
        type="button"
        onClick={() => {
          setOffline(!offline);
        }}
      >
        toggle
      </button>
      <button type="button" onClick={retry}>
        retry
      </button>
    </div>
  );
}

it('toggles the offline state and runs the retry hook', async () => {
  const onRetry = vi.fn();
  render(
    <OfflineProvider onRetry={onRetry}>
      <Probe />
    </OfflineProvider>,
  );
  expect(screen.getByText('online')).toBeInTheDocument();
  await userEvent.click(screen.getByText('toggle'));
  expect(screen.getByText('offline')).toBeInTheDocument();
  await userEvent.click(screen.getByText('retry'));
  expect(onRetry).toHaveBeenCalledOnce();
});

it('retry is harmless without a hook', async () => {
  render(
    <OfflineProvider>
      <Probe />
    </OfflineProvider>,
  );
  await userEvent.click(screen.getByText('retry'));
  expect(screen.getByText('online')).toBeInTheDocument();
});

it('can start offline', () => {
  render(
    <OfflineProvider initialOffline>
      <Probe />
    </OfflineProvider>,
  );
  expect(screen.getByText('offline')).toBeInTheDocument();
});

it('useOffline outside a provider fails loudly', () => {
  expect(() => render(<Probe />)).toThrow(/OfflineProvider/);
});
