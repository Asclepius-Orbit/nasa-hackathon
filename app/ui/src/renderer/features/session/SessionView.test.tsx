import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SessionView } from './SessionView';
import { RealDataSource } from '../../data/real/RealDataSource';
import type { RepStateView, SessionView as SessionPayload, TodayView } from '../../../shared/api';
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

describe('Stop: pain when the stop cannot be recorded', () => {
  it('stays on the session, says it was not recorded and what to do, and never goes to /stopped', async () => {
    const user = userEvent.setup();
    const { data } = setup();
    vi.spyOn(data, 'stopSessionForPain').mockRejectedValue(new Error('unavailable'));
    await user.click(await screen.findByRole('button', { name: /Stop: pain/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The stop was not recorded.');
    expect(alert).toHaveTextContent('Stop the exercise now and tell the crew medical officer. Press Urgent symptom to alert the flight surgeon.');
    expect(screen.queryByTestId('where')).not.toBeInTheDocument();
  });

  it('lets the crew try again, and goes to /stopped once it is recorded', async () => {
    const user = userEvent.setup();
    const { data } = setup();
    const spy = vi.spyOn(data, 'stopSessionForPain').mockRejectedValueOnce(new Error('unavailable'));
    await user.click(await screen.findByRole('button', { name: /Stop: pain/ }));
    await user.click(await screen.findByRole('button', { name: 'Try to record the stop again' }));
    expect(await screen.findByTestId('where')).toHaveTextContent('/stopped');
    expect(spy).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('The stop was not recorded.')).not.toBeInTheDocument();
  });

  it('does the same when the voice countdown ends and the stop cannot be recorded', async () => {
    const user = userEvent.setup();
    const { clock, data } = setup();
    vi.spyOn(data, 'stopSessionForPain').mockRejectedValue(new Error('unavailable'));
    await user.click(await screen.findByRole('button', { name: 'Mock: hear “stop, pain”' }));
    act(() => {
      clock.advance(5000);
    });
    expect(await screen.findByText(/The stop was not recorded\./)).toBeInTheDocument();
    expect(screen.queryByTestId('where')).not.toBeInTheDocument();
  });

  it('in real mode never navigates as if the stop had happened', async () => {
    const user = userEvent.setup();
    const api = new FakeAsclepiusApi();
    api.viewData.today = example<TodayView>('ui-today.nodata');
    api.viewData.sessions = { [SESSION_ID]: example<SessionPayload>('ui-session') };
    renderInWorld(
      <Routes>
        <Route path="/session" element={<SessionView />} />
        <Route path="/stopped" element={<Where />} />
      </Routes>,
      { api, data: new RealDataSource(api.views, api.reps), route: '/session' },
    );
    await user.click(await screen.findByRole('button', { name: /Stop: pain/ }));
    expect(await screen.findByText(/The stop was not recorded\./)).toBeInTheDocument();
    expect(screen.queryByTestId('where')).not.toBeInTheDocument();
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
    { api, data: new RealDataSource(api.views, api.reps), route: '/session' },
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

  describe('live rep counter', () => {
    const REP_SID = SESSION_ID;
    async function openReps(session = example<SessionPayload>('ui-session')) {
      const world = realSetup({ today: example<TodayView>('ui-today.nodata'), session });
      await screen.findByRole('heading', { level: 1, name: 'Standing lat pulldown with band' });
      await userEvent.click(screen.getByRole('button', { name: 'Do the reps' }));
      return world;
    }
    const push = (api: FakeAsclepiusApi, event: Parameters<FakeAsclepiusApi['emitReps']>[1]): void => {
      act(() => {
        api.emitReps(REP_SID, event);
      });
    };
    const rep = (name: string): RepStateView => example<RepStateView>(name);

    it('before the first event shows no counter, bar or phase, only that nothing is live yet', async () => {
      const { scheduler, api } = await openReps();
      expect(screen.getByText('No live rep count yet.')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(scheduler.active).toBe(0);
      expect(api.repsListenerCount(REP_SID)).toBe(1);
    });

    it('with signal: rep of total, phase, bar and last rep with its duration', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: rep('ui-rep-state') });
      expect(screen.getByText('Pulling down')).toBeInTheDocument();
      expect(screen.getByText('of 10')).toBeInTheDocument();
      expect(screen.getByRole('progressbar', { name: 'Movement progress' })).toHaveAttribute('aria-valuenow', '42.5');
      expect(screen.getByText('Rep 2, 4.2 s, recorded by the harness')).toBeInTheDocument();
    });

    it('first rep: no bar and no false 0 %, no last rep yet', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: rep('ui-rep-state.first-rep') });
      expect(screen.getByText('Pulling down')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(screen.queryByText(/0\s?%/)).not.toBeInTheDocument();
      expect(screen.getByText('No rep counted yet')).toBeInTheDocument();
    });

    it('resting has its own text and no bar', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: rep('ui-rep-state.resting') });
      expect(screen.getByText('Resting between reps')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    });

    it('an unknown total shows the rep without "of N"', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: { ...rep('ui-rep-state'), total: null } });
      expect(screen.getByText('Pulling down')).toBeInTheDocument();
      expect(screen.queryByText(/^of /)).not.toBeInTheDocument();
    });

    it('no signal: explicit visible notice, last known count marked as not live, no bar or phase', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: rep('ui-rep-state') });
      push(api, { kind: 'state', state: rep('ui-rep-state.no-signal') });
      expect(screen.getByRole('status')).toHaveTextContent('No signal from the sensor');
      expect(screen.getByText('Last count, not live')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(screen.queryByText('Pulling down')).not.toBeInTheDocument();
    });

    it('lost: says the monitor is being reconnected and keeps the last count as not live', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: rep('ui-rep-state') });
      push(api, { kind: 'lost' });
      expect(screen.getByRole('status')).toHaveTextContent('Reconnecting to the monitor');
      expect(screen.getByText('Last count, not live')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      push(api, { kind: 'state', state: rep('ui-rep-state') });
      expect(screen.queryByText('Reconnecting to the monitor')).not.toBeInTheDocument();
      expect(screen.getByText('Pulling down')).toBeInTheDocument();
    });

    it('end shows the final count (rep, total, last) and nothing that looks live', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: rep('ui-rep-state') });
      push(api, { kind: 'end', state: { ...rep('ui-rep-state'), phase: null, progress_pct: null } });
      expect(screen.getByText('Final count')).toBeInTheDocument();
      expect(screen.getByText('of 10').parentElement).toHaveTextContent('3 of 10');
      expect(screen.getByText('Rep 2, 4.2 s, recorded by the harness')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(screen.queryByText('Pulling down')).not.toBeInTheDocument();
      expect(screen.queryByText('No signal from the sensor')).not.toBeInTheDocument();
      expect(screen.queryByText('Last count, not live')).not.toBeInTheDocument();
      expect(screen.queryByText('Counting by')).not.toBeInTheDocument();
      expect(screen.queryByText('Backup')).not.toBeInTheDocument();
      expect(api.repsListenerCount(REP_SID)).toBe(0);
    });

    it('a live state without phase (before end) shows the rep with no phase and no bar', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: { ...rep('ui-rep-state'), phase: null, progress_pct: 40 } });
      expect(screen.getByText('of 10').parentElement).toHaveTextContent('3 of 10');
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(screen.queryByText('No signal from the sensor')).not.toBeInTheDocument();
      expect(screen.queryByText('Final count')).not.toBeInTheDocument();
    });

    it('a rep past the total shows the number without "of N"', async () => {
      const { api } = await openReps();
      push(api, { kind: 'state', state: { ...rep('ui-rep-state'), rep: 11 } });
      expect(screen.getByText('11')).toBeInTheDocument();
      expect(screen.queryByText('of 10')).not.toBeInTheDocument();
    });

    it('lost before any count: says it is reconnecting and draws no number', async () => {
      const { api } = await openReps();
      push(api, { kind: 'lost' });
      expect(screen.getByRole('status')).toHaveTextContent('Reconnecting to the monitor');
      expect(screen.queryByText('0')).not.toBeInTheDocument();
      expect(screen.queryByText('Last count, not live')).not.toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    });

    it.each(['no_reps', 'not_found'] as const)('error %s: no counter, says so', async (code) => {
      const { api } = await openReps();
      push(api, { kind: 'error', code });
      expect(screen.getByText('No live rep count yet.')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    });

    it('error unavailable: explicit state, not a counter', async () => {
      const { api } = await openReps();
      push(api, { kind: 'error', code: 'unavailable' });
      expect(screen.getByRole('status')).toHaveTextContent('The live count is not available');
    });

    it('error locked: asks main for the session again (the existing gate)', async () => {
      const { api } = await openReps();
      const before = api.calls.filter((c) => c === 'auth.current').length;
      push(api, { kind: 'error', code: 'locked' });
      expect(api.calls.filter((c) => c === 'auth.current').length).toBeGreaterThan(before);
    });

    it('unsubscribes on unmount', async () => {
      const { api, unmount } = await openReps();
      unmount();
      expect(api.repsListenerCount(REP_SID)).toBe(0);
    });

    it('a protocol without reps does not subscribe', async () => {
      const { api } = realSetup({ today: example<TodayView>('ui-today.nodata'), session: example<SessionPayload>('ui-session.spine-check') });
      await screen.findByRole('heading', { level: 1, name: 'Spine length check' });
      await userEvent.click(screen.getByRole('button', { name: 'Do the reps' }));
      expect(screen.getByText(/This protocol has no repetitions/)).toBeInTheDocument();
      expect(api.calls).not.toContain('reps.subscribe');
    });
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

describe('start against the planned time', () => {
  const sessionPlannedAt = (planned_at: string): SessionPayload => ({ ...example<SessionPayload>('ui-session'), planned_at });
  const withPlan = (planned_at: string): ReturnType<typeof renderInWorld> =>
    realSetup({ today: example<TodayView>('ui-today.nodata'), session: sessionPlannedAt(planned_at) });

  it('says how many minutes after the planned time it started', async () => {
    withPlan('2026-11-13T09:55:00Z');
    expect(await screen.findByText('Started 10:02 GMT, 7 min after the planned 09:55.')).toBeInTheDocument();
  });

  it('says how many minutes before the planned time it started', async () => {
    withPlan('2026-11-13T10:10:00Z');
    expect(await screen.findByText('Started 10:02 GMT, 7 min before the planned 10:10.')).toBeInTheDocument();
  });

  it('says it started at the planned time when the difference is under a minute', async () => {
    withPlan('2026-11-13T10:02:10Z');
    expect(await screen.findByText('Started 10:02 GMT, at the planned 10:02.')).toBeInTheDocument();
  });
});
