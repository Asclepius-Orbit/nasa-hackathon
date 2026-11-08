import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SessionView } from './SessionView';
import { RealDataSource } from '../../data/real/RealDataSource';
import type { SessionView as SessionPayload, TodayView } from '../../../shared/api';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
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
    expect(screen.queryByText(/Stretched/)).not.toBeInTheDocument();
    expect(screen.getByText('10 reps')).toBeInTheDocument();
    expect(screen.getByText('Started 13:38 GMT.')).toBeInTheDocument();
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
    expect(screen.getByText('Clean the skin')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next step' }));
    expect(screen.getByRole('heading', { name: '2. Check the harness is sending' })).toBeInTheDocument();
    expect(screen.getByText('Weak')).toBeInTheDocument();
    expect(screen.getByText('EMG right latissimus')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next step' }));
    expect(screen.getByRole('heading', { name: '3. Calibrate maximum effort' })).toBeInTheDocument();
    expect(screen.getByText('Calibration saved at 13:37 GMT.')).toBeInTheDocument();
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

const SESSION_ID = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';

function realSetup(options: { today?: TodayView; session?: SessionPayload; today404?: boolean } = {}): ReturnType<typeof renderInWorld> {
  const api = new FakeAsclepiusApi();
  if (options.today) api.viewData.today = options.today;
  if (options.session) api.viewData.sessions = { [SESSION_ID]: options.session };
  return renderInWorld(
    <Routes>
      <Route path="/session" element={<SessionView />} />
    </Routes>,
    { api, data: new RealDataSource(api.views), route: '/session' },
  );
}

describe('with the backend views', () => {
  it('writes the session from codes: protocol title, band, reps, checks and calibration', async () => {
    const user = userEvent.setup();
    realSetup({ today: example<TodayView>('ui-today.nodata'), session: example<SessionPayload>('ui-session') });
    expect(await screen.findByRole('heading', { level: 1, name: 'Standing lat pulldown with band' })).toBeInTheDocument();
    expect(screen.getByText('Band blue, 15 kg nominal')).toBeInTheDocument();
    expect(screen.getByText('10 reps')).toBeInTheDocument();
    expect(screen.getByText('Started 10:02 GMT.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Check the harness is sending' }));
    expect(screen.getByText('Stretch cord L1 to L5')).toBeInTheDocument();
    expect(screen.getByText('Weak')).toBeInTheDocument();
    expect(screen.getByText(/The sensor stopped sending/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Calibrate maximum effort' }));
    expect(screen.getByText('Calibration saved at 10:00 GMT.')).toBeInTheDocument();
  });

  it('a protocol without band, reps or rest shows none of them, and never a zero', async () => {
    const user = userEvent.setup();
    realSetup({ today: example<TodayView>('ui-today.nodata'), session: example<SessionPayload>('ui-session.spine-check') });
    expect(await screen.findByRole('heading', { level: 1, name: 'Spine length check' })).toBeInTheDocument();
    expect(screen.queryByText(/kg nominal/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^d+ reps$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Started/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Check the harness is sending' }));
    expect(screen.getByText('No sensor check has been reported yet.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Calibrate maximum effort' }));
    expect(screen.getByText('No calibration command was sent yet.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Do the reps' }));
    expect(screen.getByText(/This protocol has no repetitions/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Rest and finish' }));
    expect(screen.getByText('This protocol has no rest period.')).toBeInTheDocument();
  });

  it('shows no live rep counter or progress bar: there is no live signal from the backend', async () => {
    const { scheduler } = realSetup({ today: example<TodayView>('ui-today.nodata'), session: example<SessionPayload>('ui-session') });
    await screen.findByRole('heading', { level: 1, name: 'Standing lat pulldown with band' });
    expect(screen.getByText('No live rep count yet.')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText('Pulling down')).not.toBeInTheDocument();
    expect(scheduler.active).toBe(0);
  });

  it('says so when no session is in progress, without inventing one', async () => {
    realSetup({ today: example<TodayView>('ui-today') });
    expect(await screen.findByRole('status')).toHaveTextContent('No session is in progress.');
    expect(screen.queryByRole('button', { name: /Stop: pain/ })).not.toBeInTheDocument();
  });

  it('says there is no connection to the monitor when the backend is down', async () => {
    realSetup();
    expect(await screen.findByTestId('no-monitor')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Stop: pain/ })).not.toBeInTheDocument();
  });
});
