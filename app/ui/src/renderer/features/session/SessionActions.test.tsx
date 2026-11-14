import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';

import type { CordHistoryView, CordProtocolsView, RepStateView, SessionView as SessionPayload, StartSessionResult, TodayView } from '../../../shared/api';
import { MockDataSource } from '../../data/mock/MockDataSource';
import { RealDataSource } from '../../data/real/RealDataSource';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { FakeScheduler } from '../../test/fake-scheduler';
import { renderInWorld } from '../../test/providers';
import { SessionView } from './SessionView';

const SESSION_ID = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';
const STARTED_ID = 'started-session';

function Where(): React.JSX.Element {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

const view = (state: SessionPayload['state'], id = SESSION_ID): SessionPayload => ({ ...example<SessionPayload>('ui-session'), session_id: id, state });

function world(options: { today: 'none' | 'running'; sessions?: Record<string, SessionPayload>; route?: string }): ReturnType<typeof renderInWorld> {
  const api = new FakeAsclepiusApi();
  api.viewData.today = example<TodayView>(options.today === 'none' ? 'ui-today' : 'ui-today.nodata');
  api.viewData.sessions = options.sessions ?? {};
  api.viewData.cordProtocols = example<CordProtocolsView>('ui-cord-protocols');
  api.viewData.cordHistory = example<CordHistoryView>('ui-cord-history');
  return renderInWorld(
    <Routes>
      <Route path="/session" element={<SessionView />} />
      <Route path="/stopped" element={<Where />} />
      <Route path="/settings" element={<Where />} />
      <Route path="/profile" element={<Where />} />
    </Routes>,
    { api, data: new RealDataSource(api.views, api.reps, api), route: options.route ?? '/session' },
  );
}

const startWorld = (): ReturnType<typeof renderInWorld> => world({ today: 'none', sessions: { [STARTED_ID]: view('starting', STARTED_ID) } });
const runningWorld = (): ReturnType<typeof renderInWorld> => world({ today: 'running', sessions: { [SESSION_ID]: view('running') } });

const choose = async (name: string): Promise<void> => {
  await userEvent.click(await screen.findByRole('radio', { name }));
};
const startButton = (): HTMLElement => screen.getByRole('button', { name: 'Start session' });
/** Waits for the screen to follow the counter (its effect may run after the heading shows), then pushes a state. */
const pushRep = async (api: FakeAsclepiusApi, rep?: number): Promise<void> => {
  await waitFor(() => {
    expect(api.repsListenerCount(SESSION_ID)).toBe(1);
  });
  act(() => {
    api.emitReps(SESSION_ID, { kind: 'state', state: { ...example<RepStateView>('ui-rep-state'), ...(rep === undefined ? {} : { rep }) } });
  });
};

describe('starting a session', () => {
  it('offers the cord sessions as two independent protocols and marks the one that is due', async () => {
    const { api } = startWorld();
    const group = await screen.findByRole('radiogroup', { name: 'Choose the session' });
    expect(within(group).getByRole('radio', { name: 'Cord resistance, back straight' })).toBeInTheDocument();
    await userEvent.click(within(group).getByRole('radio', { name: 'Cord resistance, maximum flexion (due)' }));
    await userEvent.click(startButton());
    expect(api.lastArg['sessions.start']).toEqual({ protocolId: 'spine_resistance_max_flexion_v1' });
  });

  it('shows the last valid measurement of each cord session and labels the estimate as calculated, with its assumptions', async () => {
    startWorld();
    const panel = await screen.findByTestId('cord-sessions');
    expect(within(panel).getByText('4500 ohm, 20 Jan 09:00 GMT')).toBeInTheDocument();
    expect(within(panel).getAllByText('5650 ohm, 8 Feb 09:00 GMT').length).toBeGreaterThan(0);
    const estimate = within(panel).getByTestId('cord-estimate');
    expect(within(estimate).getByText('Calculated length estimate')).toBeInTheDocument();
    expect(within(estimate).getByText(/not a measured length and not clinically validated/)).toBeInTheDocument();
    expect(within(estimate).getByText(/does not change your level/)).toBeInTheDocument();
    expect(within(estimate).getByText('The contacts add no resistance.')).toBeInTheDocument();
    // The posture without a measured contact distance gets no estimate, and says why.
    expect(within(panel).getByText(/distance between the contacts in this posture has not been measured/)).toBeInTheDocument();
  });

  it.each([
    ['spine_resistance_straight_v1', 'Cord resistance, back straight'],
    ['spine_resistance_max_flexion_v1', 'Cord resistance, maximum flexion (due)'],
  ])('preselects the protocol named by ?protocol=%s, as a cord task card sends it', async (protocolId, name) => {
    world({ today: 'none', sessions: {}, route: `/session?protocol=${protocolId}` });
    expect(await screen.findByRole('radio', { name })).toBeChecked();
    expect(startButton()).toBeEnabled();
  });

  it('preselects nothing for an unknown ?protocol', async () => {
    world({ today: 'none', sessions: {}, route: '/session?protocol=nope' });
    expect(await screen.findByRole('radio', { name: 'Cord resistance, back straight' })).not.toBeChecked();
    expect(startButton()).toBeDisabled();
  });

  it('lists the cord measurements per posture, valid or not, with the reason and without any estimate', async () => {
    startWorld();
    const sections = await screen.findAllByTestId('cord-history');
    const [straightSection, flexionSection] = sections;
    if (straightSection === undefined || flexionSection === undefined) throw new Error('both postures expected');
    expect(sections).toHaveLength(2);
    const straight = within(straightSection);
    expect(straight.getAllByRole('row')).toHaveLength(4);
    expect(straight.getByText('The signal quality was below the minimum.')).toBeInTheDocument();
    expect(straight.getByText('5100 ohm')).toBeInTheDocument();
    expect(within(flexionSection).getByText('The session did not complete.')).toBeInTheDocument();
    expect(straight.queryByText(/estimate/i)).not.toBeInTheDocument();
  });

  it('keeps starting sessions possible when the cord sessions cannot be read', async () => {
    const api = new FakeAsclepiusApi();
    api.viewData.today = example<TodayView>('ui-today');
    api.viewData.sessions = {};
    api.viewErrors.cordProtocols = 'unavailable';
    renderInWorld(<SessionView />, { api, data: new RealDataSource(api.views, api.reps, api), route: '/session' });
    expect(await screen.findByRole('radio', { name: /Standing lat pulldown/ })).toBeInTheDocument();
    expect(screen.queryByTestId('cord-sessions')).not.toBeInTheDocument();
  });

  it('offers the two protocols when there is no session, and Start stays disabled until one is chosen', async () => {
    startWorld();
    const group = await screen.findByRole('radiogroup', { name: 'Choose the session' });
    expect(within(group).getByRole('radio', { name: /Standing lat pulldown with band/ })).toBeInTheDocument();
    expect(within(group).getByRole('radio', { name: /Spine length check/ })).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    await choose('Spine length check');
    expect(startButton()).toBeEnabled();
  });

  it('starts the chosen protocol, then follows the new session and says it is starting', async () => {
    const { api } = startWorld();
    await choose('Standing lat pulldown with band');
    await userEvent.click(startButton());
    expect(api.lastArg['sessions.start']).toEqual({ protocolId: 'lat_pulldown_standing_v1' });
    expect(await screen.findByText('Starting. Waiting for the harness to confirm.')).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Choose the session' })).not.toBeInTheDocument();
  });

  it('shows it is busy while the start is on its way', async () => {
    const { api } = startWorld();
    let release: (value: StartSessionResult) => void = () => undefined;
    api.sessions.start = () =>
      new Promise<StartSessionResult>((resolve) => {
        release = resolve;
      });
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(screen.getByRole('button', { name: 'Starting' })).toBeDisabled();
    act(() => {
      release({ ok: true, value: { sessionId: STARTED_ID, state: 'starting' } });
    });
    expect(await screen.findByText('Starting. Waiting for the harness to confirm.')).toBeInTheDocument();
  });

  it('without a paired harness says so and links to the harness section of Settings', async () => {
    const { api } = startWorld();
    api.forced.set('sessions.start', { ok: false, reason: 'no_harness' });
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(await screen.findByRole('alert')).toHaveTextContent('No harness is paired to you.');
    await userEvent.click(screen.getByRole('link', { name: 'Pair a harness in Settings' }));
    expect(await screen.findByTestId('where')).toHaveTextContent('/settings');
  });

  it('without a daily context (refused with no field) points to the profile', async () => {
    const { api } = startWorld();
    api.forced.set('sessions.start', { ok: false, reason: 'invalid', fields: [] });
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(await screen.findByRole('alert')).toHaveTextContent('The monitor needs today’s mission context before a session can start.');
    await userEvent.click(screen.getByRole('link', { name: 'Set it in Profile' }));
    expect(await screen.findByTestId('where')).toHaveTextContent('/profile');
  });

  it('says the harness is busy or offline on a conflict, and lets the crew try again', async () => {
    const { api } = startWorld();
    api.forced.set('sessions.start', { ok: false, reason: 'conflict' });
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(await screen.findByRole('alert')).toHaveTextContent('The harness is busy or offline.');
    expect(startButton()).toBeEnabled();
  });

  it.each([
    ['locked', 'The app is locked. Unlock it, then start again.'],
    ['unavailable', 'The monitor did not answer. Nothing was started; try again.'],
  ] as const)('says what happened on %s', async (reason, text) => {
    const { api } = startWorld();
    api.forced.set('sessions.start', { ok: false, reason });
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
  });

  it('says the monitor did not answer when the bridge itself fails', async () => {
    const { api } = startWorld();
    api.rejecting.add('sessions.start');
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(await screen.findByRole('alert')).toHaveTextContent('The monitor did not answer. Nothing was started; try again.');
  });

  it('shows a rejected session with a retry that starts the same protocol again', async () => {
    const { api } = startWorld();
    api.viewData.sessions = { [STARTED_ID]: view('rejected', STARTED_ID) };
    await choose('Standing lat pulldown with band');
    await userEvent.click(startButton());
    expect(await screen.findByText('The harness did not accept the session.')).toBeInTheDocument();
    api.viewData.sessions = { [STARTED_ID]: view('starting', STARTED_ID) };
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(api.calls.filter((c) => c === 'sessions.start')).toHaveLength(2);
    expect(api.lastArg['sessions.start']).toEqual({ protocolId: 'lat_pulldown_standing_v1' });
    expect(await screen.findByText('Starting. Waiting for the harness to confirm.')).toBeInTheDocument();
  });

  it('offers to start another once the session has ended', async () => {
    const { api } = startWorld();
    api.viewData.sessions = { [STARTED_ID]: view('ended', STARTED_ID) };
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(await screen.findByRole('heading', { name: 'Start another session' })).toBeInTheDocument();
    expect(screen.getByRole('radiogroup', { name: 'Choose the session' })).toBeInTheDocument();
  });

  it('in mock mode, after a stop the screen offers a start and the new session shows as starting', async () => {
    const data = new MockDataSource({ now: () => new Date('2026-11-05T13:40:00Z'), scheduler: new FakeScheduler() });
    await data.stopSessionForPain('mock-session', 4);
    renderInWorld(<SessionView />, { data, route: '/session' });
    await choose('Spine length check');
    await userEvent.click(startButton());
    expect(await screen.findByText('Starting. Waiting for the harness to confirm.')).toBeInTheDocument();
  });
});

describe('processing failed', () => {
  const failed = (): ReturnType<typeof renderInWorld> => world({ today: 'running', sessions: { [SESSION_ID]: view('processing_failed') } });

  it('offers to reprocess and sends it for the session', async () => {
    const { api } = failed();
    expect(await screen.findByText('The analysis of this session failed.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reprocess' }));
    expect(api.lastArg['sessions.reprocess']).toEqual({ sessionId: SESSION_ID });
    expect(await screen.findByText('Reprocessing. The result shows here when it is done.')).toBeInTheDocument();
  });

  it('on a conflict says the session is no longer failed', async () => {
    const { api } = failed();
    api.forced.set('sessions.reprocess', { ok: false, reason: 'conflict' });
    await userEvent.click(await screen.findByRole('button', { name: 'Reprocess' }));
    expect(await screen.findByText('This session is no longer failed.')).toBeInTheDocument();
  });

  it('on any other refusal says it was not sent, and keeps the button', async () => {
    const { api } = failed();
    api.forced.set('sessions.reprocess', { ok: false, reason: 'unavailable' });
    await userEvent.click(await screen.findByRole('button', { name: 'Reprocess' }));
    expect(await screen.findByText('The monitor did not answer. Nothing was sent; try again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reprocess' })).toBeEnabled();
  });
});

describe('stage buttons', () => {
  it('Finish is enabled while the session runs and stops it as completed', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: 'Rest and finish' }));
    await userEvent.click(screen.getByRole('button', { name: 'Finish session' }));
    expect(api.lastArg['sessions.stop']).toEqual({ sessionId: SESSION_ID, reason: 'completed' });
    expect(await screen.findByText('Finish sent. The session is being saved.')).toBeInTheDocument();
  });

  it('Finish says nothing was sent when the backend refuses', async () => {
    const { api } = runningWorld();
    api.forced.set('sessions.stop', { ok: false, reason: 'conflict' });
    await userEvent.click(await screen.findByRole('button', { name: 'Rest and finish' }));
    await userEvent.click(screen.getByRole('button', { name: 'Finish session' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The harness is busy or the session is not running any more. Nothing was sent.');
  });

  it('Finish and Calibrate stay disabled while the session has not started running', async () => {
    world({ today: 'running', sessions: { [SESSION_ID]: view('starting') } });
    await userEvent.click(await screen.findByRole('button', { name: 'Rest and finish' }));
    expect(screen.getByRole('button', { name: 'Finish session' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Calibrate maximum effort' }));
    expect(screen.getByRole('button', { name: 'Calibrate' })).toBeDisabled();
  });

  it('Calibrate asks the harness for the maximum-effort calibration', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: 'Calibrate maximum effort' }));
    await userEvent.click(screen.getByRole('button', { name: 'Calibrate' }));
    expect(api.lastArg['sessions.calibrateMvc']).toEqual({ sessionId: SESSION_ID });
    expect(await screen.findByText('Calibration requested. Waiting for the harness to confirm.')).toBeInTheDocument();
  });

  it('Calibrate says nothing was sent when the monitor does not answer', async () => {
    const { api } = runningWorld();
    api.forced.set('sessions.calibrateMvc', { ok: false, reason: 'unavailable' });
    await userEvent.click(await screen.findByRole('button', { name: 'Calibrate maximum effort' }));
    await userEvent.click(screen.getByRole('button', { name: 'Calibrate' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The monitor did not answer. Nothing was sent; try again.');
  });

  it('says the app is locked, or that the request was refused, with the matching text', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: 'Calibrate maximum effort' }));
    api.forced.set('sessions.calibrateMvc', { ok: false, reason: 'locked' });
    await userEvent.click(screen.getByRole('button', { name: 'Calibrate' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The app is locked. Unlock it, then try again.');
    api.forced.set('sessions.calibrateMvc', { ok: false, reason: 'invalid', fields: [] });
    await userEvent.click(screen.getByRole('button', { name: 'Calibrate' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The monitor refused the request. Nothing was sent.');
  });

  it('says nothing was sent when the bridge itself fails', async () => {
    const { api } = runningWorld();
    api.rejecting.add('sessions.calibrateMvc');
    await userEvent.click(await screen.findByRole('button', { name: 'Calibrate maximum effort' }));
    await userEvent.click(screen.getByRole('button', { name: 'Calibrate' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The monitor did not answer. Nothing was sent; try again.');
  });

  it('marks an artefact at the current rep of the live counter', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: 'Do the reps' }));
    await pushRep(api);
    await userEvent.click(screen.getByRole('button', { name: 'Mark an artefact' }));
    expect(api.lastArg['sessions.markArtifact']).toEqual({ sessionId: SESSION_ID, repIndex: 3 });
    expect(await screen.findByText('Artefact marked.')).toBeInTheDocument();
  });

  it('marks an artefact with no rep when the counter is on rep 0, never a made-up one', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: 'Do the reps' }));
    await pushRep(api, 0);
    await userEvent.click(screen.getByRole('button', { name: 'Mark an artefact' }));
    expect(api.lastArg['sessions.markArtifact']).toEqual({ sessionId: SESSION_ID, repIndex: null });
  });

  it('leaves "Recount this rep" as it was: it sends nothing yet', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: 'Do the reps' }));
    await pushRep(api);
    await userEvent.click(screen.getByRole('button', { name: 'Recount this rep' }));
    expect(api.calls.filter((c) => c.startsWith('sessions.'))).toEqual([]);
  });
});

describe('Stop: pain with the backend', () => {
  it('sends the rep of the live counter and goes to /stopped once it is recorded', async () => {
    const { api } = runningWorld();
    await screen.findByRole('heading', { level: 1, name: 'Standing lat pulldown with band' });
    await pushRep(api);
    await userEvent.click(screen.getByRole('button', { name: /Stop: pain/ }));
    expect(api.lastArg['sessions.stop']).toEqual({ sessionId: SESSION_ID, reason: 'aborted_pain', repIndex: 3 });
    expect(await screen.findByTestId('where')).toHaveTextContent('/stopped');
  });

  it('knows the rep from any stage, not only while the reps stage is open', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: 'Fit electrodes, ground, IMU and cord' }));
    await pushRep(api);
    await userEvent.click(screen.getByRole('button', { name: /Stop: pain/ }));
    expect(api.lastArg['sessions.stop']).toMatchObject({ repIndex: 3 });
  });

  it('sends no rep (null) before the counter has a rep', async () => {
    const { api } = runningWorld();
    await userEvent.click(await screen.findByRole('button', { name: /Stop: pain/ }));
    expect(api.lastArg['sessions.stop']).toEqual({ sessionId: SESSION_ID, reason: 'aborted_pain', repIndex: null });
  });

  it('sends no rep (null) when the counter says rep 0', async () => {
    const { api } = runningWorld();
    await screen.findByRole('heading', { level: 1, name: 'Standing lat pulldown with band' });
    await pushRep(api, 0);
    await userEvent.click(screen.getByRole('button', { name: /Stop: pain/ }));
    expect(api.lastArg['sessions.stop']).toMatchObject({ repIndex: null });
  });

  it('a refused stop stays on the session with the not-recorded alert, and a retry can succeed', async () => {
    const { api } = runningWorld();
    api.forced.set('sessions.stop', { ok: false, reason: 'unavailable' });
    await userEvent.click(await screen.findByRole('button', { name: /Stop: pain/ }));
    expect(await screen.findByText('The stop was not recorded.')).toBeInTheDocument();
    expect(screen.queryByTestId('where')).not.toBeInTheDocument();
    api.forced.delete('sessions.stop');
    await userEvent.click(screen.getByRole('button', { name: 'Try to record the stop again' }));
    expect(await screen.findByTestId('where')).toHaveTextContent('/stopped');
  });

  it('shows no alert while nothing has been done', async () => {
    runningWorld();
    await screen.findByRole('button', { name: /Stop: pain/ });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('after a cord session', () => {
  const ended = (state: SessionPayload['state'], protocolId: string): ReturnType<typeof renderInWorld> =>
    world({ today: 'running', sessions: { [SESSION_ID]: { ...view(state), protocol_id: protocolId } } });

  it('shows the cord estimate, labelled as calculated, with the measurements once the session is over', async () => {
    ended('processed', 'spine_resistance_straight_v1');
    const panel = await screen.findByTestId('cord-sessions');
    const estimate = within(panel).getByTestId('cord-estimate');
    expect(within(estimate).getByText(/not a measured length and not clinically validated/)).toBeInTheDocument();
    expect(within(panel).getAllByText('5650 ohm, 8 Feb 09:00 GMT').length).toBeGreaterThan(0);
  });

  it('does not show it while the cord session is still running, nor after another protocol', async () => {
    const running = ended('running', 'spine_resistance_straight_v1');
    await screen.findByTestId('session-view');
    await screen.findByRole('heading', { name: /Cord resistance/ });
    expect(screen.queryByTestId('cord-sessions')).not.toBeInTheDocument();
    running.unmount();
    ended('processed', 'lat_pulldown_standing_v1');
    await screen.findByRole('heading', { name: 'Start another session' });
    expect(screen.queryByTestId('cord-sessions')).not.toBeInTheDocument();
  });
});
