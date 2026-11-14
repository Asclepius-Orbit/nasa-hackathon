import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { StripView } from '../../../shared/api';
import { RealDataSource } from '../../data/real/RealDataSource';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { StoppedView } from './StoppedView';

const setup = async (): Promise<ReturnType<typeof renderInWorld>> => {
  const world = renderInWorld(<StoppedView />, { route: '/stopped' });
  await screen.findByText(/The harness stopped recording at rep/);
  return world;
};

function realWorld(): { api: FakeAsclepiusApi; data: RealDataSource; world: () => ReturnType<typeof renderInWorld> } {
  const api = new FakeAsclepiusApi();
  api.viewData.strip = example<StripView>('ui-strip');
  const data = new RealDataSource(api.views, api.reps, api);
  return { api, data, world: () => renderInWorld(<StoppedView />, { api, data, route: '/stopped' }) };
}

describe('with the mock', () => {
  it('says where the session stopped, what is still saved and the ground link', async () => {
    await setup();
    expect(screen.getByText(/The harness stopped recording at rep 4\./)).toBeInTheDocument();
    expect(screen.getByText(/saved with the reason “stopped for pain” and its data still counts/)).toBeInTheDocument();
    expect(screen.getByText('The ground link is down for 12 min.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'What happens next' })).toBeInTheDocument();
    expect(screen.getByText(/Do not restart the session today unless the flight surgeon clears it\./)).toBeInTheDocument();
  });

  it('no longer asks for pain or symptoms and has nothing to save', async () => {
    await setup();
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
    expect(screen.queryByText('How much does it hurt now?')).not.toBeInTheDocument();
    expect(screen.queryByText('Do you have any of these now?')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save and run the assessment' })).not.toBeInTheDocument();
    expect(screen.queryByText(/crew medical officer is on board/)).not.toBeInTheDocument();
    expect(screen.queryByText(/your answers/)).not.toBeInTheDocument();
  });

  it('logs a call to the crew medical officer in the contact list', async () => {
    const { data } = await setup();
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Call the crew medical officer' }));
    expect(await screen.findByText(/call to the crew medical officer logged/)).toBeInTheDocument();
    expect((await data.getStopped()).contacts).toHaveLength(1);
  });

  it('"Message the flight surgeon" opens Urgent with nothing marked', async () => {
    await setup();
    await userEvent.click(screen.getByRole('button', { name: 'Message the flight surgeon' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('says the call could not be logged when the data source refuses, and adds nothing to the list', async () => {
    const { data } = await setup();
    vi.spyOn(data, 'logCrewMedicalOfficerCall').mockRejectedValue(new Error('unavailable'));
    await userEvent.click(screen.getByRole('button', { name: 'Call the crew medical officer' }));
    expect(await screen.findByText('The call could not be logged. Write it down and tell the flight surgeon.')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Contacts today' })).not.toBeInTheDocument();
  });
});

describe('with the backend', () => {
  it('shows the rep the pain stop was sent at, the ground link and the contacts the backend holds', async () => {
    const { api, data, world } = realWorld();
    api.contactsValue = [{ contactId: 'c1', at: '2026-11-05T13:52:00Z', sessionId: 'S-1' }];
    await data.stopSessionForPain('S-1', 6);
    world();
    expect(await screen.findByText(/The harness stopped recording at rep 6\./)).toBeInTheDocument();
    expect(screen.getByText('The ground link is down for 12 min.')).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Contacts today' })).getByText('13:52 GMT: call to the crew medical officer logged.')).toBeInTheDocument();
  });

  it('with an unknown rep says so and never shows a number', async () => {
    const { data, world } = realWorld();
    await data.stopSessionForPain('S-1', null);
    world();
    expect(await screen.findByText(/The rep it stopped at is not known\./)).toBeInTheDocument();
    expect(screen.queryByText(/at rep/)).not.toBeInTheDocument();
  });

  it('when this app stopped nothing does not claim a stop was saved', async () => {
    realWorld().world();
    expect(await screen.findByText(/This app has not stopped a session since it was opened\./)).toBeInTheDocument();
    expect(screen.queryByText(/saved with the reason/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Call the crew medical officer' })).toBeInTheDocument();
  });

  it('an unknown ground link is said to be unknown, never up', async () => {
    const { api, world } = realWorld();
    api.viewData.strip = { ...example<StripView>('ui-strip'), ground_link: null };
    world();
    expect(await screen.findByText('The state of the ground link is not known.')).toBeInTheDocument();
  });

  it('logs the call for the stopped session and lists it', async () => {
    const { api, data, world } = realWorld();
    await data.stopSessionForPain('S-1', 6);
    world();
    await userEvent.click(await screen.findByRole('button', { name: 'Call the crew medical officer' }));
    expect(await screen.findByText('13:52 GMT: call to the crew medical officer logged.')).toBeInTheDocument();
    expect(api.lastArg['contacts.logOfficerCall']).toEqual({ sessionId: 'S-1' });
  });

  it('says the call could not be logged when the backend refuses it', async () => {
    const { api, world } = realWorld();
    api.forced.set('contacts.logOfficerCall', { ok: false, reason: 'unavailable' });
    world();
    await userEvent.click(await screen.findByRole('button', { name: 'Call the crew medical officer' }));
    expect(await screen.findByText('The call could not be logged. Write it down and tell the flight surgeon.')).toBeInTheDocument();
  });

  it('says there is no connection to the monitor when it cannot be read', async () => {
    const { api, world } = realWorld();
    api.viewErrors.strip = 'unavailable';
    world();
    expect(await screen.findByTestId('no-monitor')).toBeInTheDocument();
  });
});
