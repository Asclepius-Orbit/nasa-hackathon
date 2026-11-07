import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SessionView } from './SessionView';
import { renderInWorld } from '../../test/providers';

function Where(): React.JSX.Element {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

function setup(route = '/session') {
  return renderInWorld(
    <Routes>
      <Route path="/session" element={<SessionView />} />
      <Route path="/stopped" element={<Where />} />
    </Routes>,
    { route },
  );
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('SessionView header', () => {
  it('shows the load, the late-start note and the lede', async () => {
    setup();
    expect(await screen.findByRole('heading', { level: 1, name: 'Standing lat pulldown with band' })).toBeInTheDocument();
    expect(screen.getByText('Band blue, 15 kg nominal')).toBeInTheDocument();
    expect(screen.getByText('Stretched 40 cm at start')).toBeInTheDocument();
    expect(screen.getByText('10 reps')).toBeInTheDocument();
    expect(screen.getByText('Started 13:38 GMT, 22 min before the planned 14:00.')).toBeInTheDocument();
  });
});

describe('Stop: pain', () => {
  it('stops the session for pain and goes to /stopped', async () => {
    const user = userEvent.setup();
    const { data } = setup();
    const spy = vi.spyOn(data, 'stopSessionForPain');
    await user.click(await screen.findByRole('button', { name: /Stop: pain/ }));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId('where')).toHaveTextContent('/stopped');
  });
});

describe('voice undo', () => {
  it('counts down 5 s with the clock and then stops for pain', async () => {
    const user = userEvent.setup();
    const { clock, data } = setup();
    const spy = vi.spyOn(data, 'stopSessionForPain');
    expect(screen.queryByText(/Heard/)).not.toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Mock: hear “stop, pain”' }));
    const alert = screen.getByRole('alert');
    expect(within(alert).getByText('Stopping in 5 s.')).toBeInTheDocument();
    act(() => {
      clock.advance(2000);
    });
    expect(within(alert).getByText('Stopping in 3 s.')).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
    act(() => {
      clock.advance(3000);
    });
    expect(await screen.findByTestId('where')).toHaveTextContent('/stopped');
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('is cancelled by "Keep going" and stops the countdown', async () => {
    const user = userEvent.setup();
    const { clock, data } = setup();
    const spy = vi.spyOn(data, 'stopSessionForPain');
    await user.click(await screen.findByRole('button', { name: 'Mock: hear “stop, pain”' }));
    await user.click(screen.getByRole('button', { name: 'Keep going, it was not me' }));
    expect(screen.queryByText(/Heard/)).not.toBeInTheDocument();
    act(() => {
      clock.advance(10000);
    });
    expect(spy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('where')).not.toBeInTheDocument();
  });

  it('"Stop now" stops immediately', async () => {
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole('button', { name: 'Mock: hear “stop, pain”' }));
    await user.click(screen.getByRole('button', { name: 'Stop now' }));
    expect(await screen.findByTestId('where')).toHaveTextContent('/stopped');
  });

  it('hides the mock trigger outside DEV', async () => {
    vi.stubEnv('DEV', false);
    setup();
    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByRole('button', { name: /Mock: hear/ })).not.toBeInTheDocument();
  });
});

describe('stages', () => {
  it('starts on the reps stage and lets a done step be viewed, with Back and Next', async () => {
    const user = userEvent.setup();
    setup();
    expect(await screen.findByText('of 10')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Fit electrodes, IMU and cord' }));
    expect(screen.getByRole('heading', { name: '1. Fit electrodes, IMU and cord' })).toBeInTheDocument();
    expect(screen.getByText('Fitted at 13:31 GMT and checked by a crewmate.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next step' }));
    expect(screen.getByRole('heading', { name: '2. Check the harness is sending' })).toBeInTheDocument();
    expect(screen.getByText('Weak')).toBeInTheDocument();
    expect(screen.getByText('Continued without right EMG at 13:34 GMT.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next step' }));
    expect(screen.getByRole('heading', { name: '3. Calibrate maximum effort' })).toBeInTheDocument();
    expect(screen.getByText('Pull 3')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('heading', { name: '2. Check the harness is sending' })).toBeInTheDocument();
  });

  it('opens the finish stage with Finish disabled', async () => {
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole('button', { name: 'Rest and finish' }));
    expect(screen.getByRole('heading', { name: '5. Rest and finish' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Finish session' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Next step' })).not.toBeInTheDocument();
  });

  it('honours ?stage= for screenshots', async () => {
    setup('/session?stage=mvc');
    expect(await screen.findByRole('heading', { name: '3. Calibrate maximum effort' })).toBeInTheDocument();
  });
});

describe('live reps', () => {
  it('follows subscribeReps and unsubscribes on unmount', async () => {
    const { scheduler, unmount } = setup();
    expect(await screen.findByText('Pulling down')).toBeInTheDocument();
    expect(await screen.findByText('Rep 3, 2.3 s, recorded by the harness')).toBeInTheDocument();
    expect(scheduler.active).toBe(1);
    act(() => {
      scheduler.advance(450 * 5);
    });
    expect(screen.getByText('Letting up')).toBeInTheDocument();
    act(() => {
      scheduler.advance(450 * 5);
    });
    expect(screen.getByText('Rep 4, 2.4 s, recorded by the harness')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Movement progress' })).toBeInTheDocument();
    unmount();
    expect(scheduler.active).toBe(0);
  });
});
