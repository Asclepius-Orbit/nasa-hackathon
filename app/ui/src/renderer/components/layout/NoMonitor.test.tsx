import { screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { useAuth } from '../../auth/AuthProvider';
import { isNoMonitor, LoadFailure, NoMonitor } from './NoMonitor';

function Status(): React.JSX.Element {
  return <p>status:{useAuth().status}</p>;
}

it('treats only unavailable as no connection to the monitor', () => {
  expect(isNoMonitor(new Error('unavailable'))).toBe(true);
  expect(isNoMonitor(new Error('locked'))).toBe(false);
  expect(isNoMonitor(new Error('not_found'))).toBe(false);
});

it('says there is no connection to the monitor', () => {
  renderInWorld(<NoMonitor />);
  expect(screen.getByRole('alert')).toHaveTextContent('No connection to the monitor.');
});

it('shows the no-monitor state for unavailable and the screen text for another failure', () => {
  const first = renderInWorld(<LoadFailure error={new Error('unavailable')} fallback="Could not load." />);
  expect(screen.getByTestId('no-monitor')).toBeInTheDocument();
  first.unmount();
  renderInWorld(<LoadFailure error={new Error('not_found')} fallback="Could not load." />);
  expect(screen.getByRole('alert')).toHaveTextContent('Could not load.');
  expect(screen.queryByTestId('no-monitor')).not.toBeInTheDocument();
});

it('on locked it never says the monitor is down: it re-reads the session so the lock flow takes over', async () => {
  const api = new FakeAsclepiusApi();
  let reads = 0;
  api.auth.current = () => Promise.resolve({ crewId: 'AST-01', locked: reads++ > 0 });
  renderInWorld(
    <>
      <Status />
      <LoadFailure error={new Error('locked')} fallback="Could not load." />
    </>,
    { api },
  );
  expect(await screen.findByText('status:locked')).toBeInTheDocument();
  expect(screen.queryByTestId('no-monitor')).not.toBeInTheDocument();
  expect(screen.queryByText('Could not load.')).not.toBeInTheDocument();
});
