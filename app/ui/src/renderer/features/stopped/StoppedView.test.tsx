import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { RealDataSource } from '../../data/real/RealDataSource';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { StoppedView } from './StoppedView';

const setup = async (): Promise<ReturnType<typeof renderInWorld>> => {
  const world = renderInWorld(<StoppedView />, { route: '/stopped' });
  await screen.findByText(/The harness stopped recording at rep/);
  return world;
};

it('says where the session stopped and what is still saved', async () => {
  await setup();
  expect(screen.getByText(/The harness stopped recording at rep 4\./)).toBeInTheDocument();
  expect(screen.getByText(/saved with the reason “stopped for pain” and its data still counts/)).toBeInTheDocument();
  expect(screen.getByText('The ground link is down for 12 min. The crew medical officer is on board.')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'What happens next' })).toBeInTheDocument();
  expect(screen.getByText(/Do not restart the session today unless the flight surgeon clears it\./)).toBeInTheDocument();
});

it('needs the pain answer before saving', async () => {
  await setup();
  const save = screen.getByRole('button', { name: 'Save and run the assessment' });
  expect(save).toBeDisabled();
  expect(screen.getByText('Pain now is needed to save.')).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole('radiogroup', { name: 'How much does it hurt now?' })).getByRole('radio', { name: '6' }));
  expect(save).toBeEnabled();
  await userEvent.click(save);
  expect(screen.getByRole('status')).toHaveTextContent('Saved. A new assessment is running.');
});

it('logs a call to the crew medical officer in the contact list', async () => {
  const { data } = await setup();
  expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Call the crew medical officer' }));
  expect(await screen.findByText(/call to the crew medical officer logged/)).toBeInTheDocument();
  expect((await data.getStopped()).contacts).toHaveLength(1);
});

it('opens a live alert for marked symptoms and sends it through Urgent', async () => {
  await setup();
  await userEvent.click(screen.getByRole('button', { name: 'Leg weakness' }));
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent('1 warning symptom marked.');
  await userEvent.click(within(alert).getByRole('button', { name: 'Send alert' }));
  const dialog = await screen.findByRole('dialog');
  expect(await within(dialog).findByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'true');
});

it('"Message the flight surgeon" opens Urgent with nothing marked', async () => {
  await setup();
  await userEvent.click(screen.getByRole('button', { name: 'Message the flight surgeon' }));
  const dialog = await screen.findByRole('dialog');
  expect(await within(dialog).findByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'false');
});

it('with the backend (real mode) says it is not available instead of inventing a stop', async () => {
  const api = new FakeAsclepiusApi();
  renderInWorld(<StoppedView />, { api, data: new RealDataSource(api.views), route: '/stopped' });
  expect(await screen.findByTestId('not-built')).toBeInTheDocument();
  expect(screen.getByText('Not available yet.')).toBeInTheDocument();
  expect(screen.queryByText(/did not answer/)).not.toBeInTheDocument();
  expect(screen.queryByText(/The harness stopped recording/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Call the crew medical officer' })).not.toBeInTheDocument();
});

it('says the call could not be logged when the data source refuses, and adds nothing to the list', async () => {
  const { data } = await setup();
  vi.spyOn(data, 'logCrewMedicalOfficerCall').mockRejectedValue(new Error('unavailable'));
  await userEvent.click(screen.getByRole('button', { name: 'Call the crew medical officer' }));
  expect(await screen.findByText('The call could not be logged. Write it down and tell the flight surgeon.')).toBeInTheDocument();
  expect(screen.queryByRole('list', { name: 'Contacts today' })).not.toBeInTheDocument();
});
