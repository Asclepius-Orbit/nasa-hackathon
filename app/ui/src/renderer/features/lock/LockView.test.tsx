import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';

import { useAuth } from '../../auth/AuthProvider';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { LockView } from './LockView';

function Status(): React.JSX.Element {
  return <p>status:{useAuth().status}</p>;
}

function setup(api = new FakeAsclepiusApi()) {
  api.session = { crewId: 'AST-01', locked: true };
  const world = renderInWorld(
    <>
      <Status />
      <Routes>
        <Route path="/lock" element={<LockView />} />
        <Route path="/today" element={<p>today page</p>} />
      </Routes>
    </>,
    { api, route: '/lock' },
  );
  return { ...world, user: userEvent.setup() };
}

describe('LockView', () => {
  it('shows the time, mission day and no health data', async () => {
    setup();
    expect(await screen.findByText('13:40')).toBeInTheDocument();
    expect(screen.getByText('GMT, mission day 87')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Enter your crew PIN' })).toBeInTheDocument();
    expect(screen.getByText('No health data is shown while locked.')).toBeInTheDocument();
  });

  it('unlocks with the PIN of the session crew member, digits never shown', async () => {
    const { user, api } = setup();
    await screen.findByText('status:locked');
    await user.keyboard('123');
    expect(screen.queryByText('123')).toBeNull();
    await user.click(screen.getByRole('button', { name: '4' }));
    expect(await screen.findByText('status:signed-in')).toBeInTheDocument();
    expect(await screen.findByText('today page')).toBeInTheDocument();
    expect(api.calls).toContain('auth.verifyPin');
  });

  it('reports a wrong PIN with the tries left and clears the entry', async () => {
    const { user } = setup();
    await screen.findByText('status:locked');
    await user.keyboard('0000');
    expect(await screen.findByText('That PIN does not match. 2 tries left.')).toBeInTheDocument();
    expect(screen.getByText('0 of 4 digits entered')).toBeInTheDocument();
  });

  it('reports a lockout and a failing store', async () => {
    const api = new FakeAsclepiusApi();
    api.auth.verifyPin = () => Promise.resolve({ ok: false, reason: 'locked', retryAfterMs: 300_000 });
    const { user } = setup(api);
    await screen.findByText('status:locked');
    await user.keyboard('0000');
    expect(await screen.findByText('Too many tries. Wait 5 min or ask the crew medical officer to reset your PIN.')).toBeInTheDocument();
    api.auth.verifyPin = () => Promise.reject(new Error('ipc'));
    await user.keyboard('0000');
    expect(await screen.findByText(/could not reach its secure store/)).toBeInTheDocument();
  });

  it('signs out from the lock screen so another member can use the tablet', async () => {
    const { user, api } = setup();
    await screen.findByText('status:locked');
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByText('status:signed-out')).toBeInTheDocument();
    expect(api.calls).toContain('auth.signOut');
  });

  it('keeps Urgent available while locked', async () => {
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: 'Urgent symptom, works while locked' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('does nothing without a session crew member', async () => {
    const api = new FakeAsclepiusApi();
    const user = userEvent.setup();
    renderInWorld(<LockView />, { api, signedIn: false });
    await user.keyboard('1234');
    expect(api.calls).not.toContain('auth.verifyPin');
  });
});

describe('LockView without a launch date', () => {
  it('shows the time but no mission day', async () => {
    const api = new FakeAsclepiusApi();
    const world = setup(api);
    world.data.getStrip = () =>
      Promise.resolve({
      missionDay: null,
      missionStart: null,
      flightPhase: null,
      harness: null,
      groundLink: null,
      lastContactMinAgo: null,
      reportsDue: 0,
    });
    world.unmount();
    renderInWorld(<LockView />, { api, route: '/lock', data: world.data });
    expect(await screen.findByText('13:40')).toBeInTheDocument();
    expect(screen.queryByText(/mission day/)).not.toBeInTheDocument();
  });
});
