import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import { RealDataSource } from '../../data/real/RealDataSource';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { FakeScheduler } from '../../test/fake-scheduler';
import { renderInWorld } from '../../test/providers';
import { useUrgent } from './UrgentContext';

function Opener({ codes }: { codes?: Parameters<ReturnType<typeof useUrgent>['open']>[0] }): React.JSX.Element {
  const { open } = useUrgent();
  return (
    <button
      type="button"
      onClick={() => {
        open(codes);
      }}
    >
      Urgent symptom
    </button>
  );
}

const openDialog = async (): Promise<HTMLElement> => {
  await userEvent.click(screen.getByRole('button', { name: 'Urgent symptom' }));
  return screen.findByRole('dialog', { name: 'What do you have now?' });
};

const mockData = (): MockDataSource =>
  new MockDataSource({ now: () => new Date('2026-11-05T13:40:00Z'), scheduler: new FakeScheduler() });

it('offers the eight red flags and the two escapes, and Send stays disabled until one is chosen', async () => {
  renderInWorld(<Opener />, { withRouter: false });
  const dialog = await openDialog();
  const group = await within(dialog).findByRole('group', { name: 'Symptoms' });
  expect(within(group).getAllByRole('button')).toHaveLength(10);
  expect(within(dialog).getByRole('button', { name: 'Very severe pain' })).toBeInTheDocument();
  expect(within(dialog).getByRole('button', { name: 'Something else' })).toBeInTheDocument();
  const send = within(dialog).getByRole('button', { name: 'Send' });
  expect(send).toBeDisabled();
  await userEvent.click(within(dialog).getByRole('button', { name: 'Leg weakness' }));
  expect(send).toBeEnabled();
  await userEvent.click(within(dialog).getByRole('button', { name: 'Leg weakness' }));
  expect(send).toBeDisabled();
});

it('pre-marks the symptoms passed by the caller', async () => {
  renderInWorld(<Opener codes={['leg_weakness']} />, { withRouter: false });
  const dialog = await openDialog();
  expect(await within(dialog).findByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'true');
  expect(within(dialog).getByRole('button', { name: 'Send' })).toBeEnabled();
});

it('says there is no link to the ground, then Queued once sent', async () => {
  renderInWorld(<Opener />, { withRouter: false });
  const dialog = await openDialog();
  expect(await within(dialog).findByText(/No link to the ground\./)).toBeInTheDocument();
  expect(within(dialog).getByText(/in about 12 min/)).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole('button', { name: 'Fever with back pain' }));
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
  expect(within(dialog).getByText('Queued.')).toBeInTheDocument();
  expect(within(dialog).getByRole('button', { name: 'Queued' })).toBeDisabled();
});

it('treats an unreachable data service as no link too', async () => {
  const data = mockData();
  data.getStrip = () => Promise.reject(new Error('down'));
  renderInWorld(<Opener />, { withRouter: false, data, initialOffline: true });
  const dialog = await openDialog();
  expect(within(dialog).getByText(/No link to the ground\./)).toBeInTheDocument();
});

it('announces the link is up when the ground can be reached', async () => {
  const data = mockData();
  const real = data.getStrip.bind(data);
  data.getStrip = async () => ({ ...(await real()), groundLink: { state: 'up' } });
  renderInWorld(<Opener />, { withRouter: false, data });
  const dialog = await openDialog();
  expect(await within(dialog).findByText(/Link to the ground is up\./)).toBeInTheDocument();
});

it('never promises delivery, even with the link up: it says Queued and no Sent', async () => {
  const data = mockData();
  const real = data.getStrip.bind(data);
  data.getStrip = async () => ({ ...(await real()), groundLink: { state: 'up' } });
  renderInWorld(<Opener />, { withRouter: false, data });
  const dialog = await openDialog();
  expect(await within(dialog).findByText(/Link to the ground is up\./)).toBeInTheDocument();
  expect(within(dialog).queryByText(/as soon as you send/)).not.toBeInTheDocument();
  expect(dialog).toHaveTextContent(/queued/i);
  await userEvent.click(within(dialog).getByRole('button', { name: 'Fever with back pain' }));
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
  expect(within(dialog).getByText('Queued.')).toBeInTheDocument();
  expect(within(dialog).queryByText(/goes to the flight surgeon/)).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Sent' })).not.toBeInTheDocument();
});

it('hides the "For:" picker while signed in', async () => {
  renderInWorld(<Opener />, { withRouter: false });
  const dialog = await openDialog();
  await within(dialog).findByRole('button', { name: 'Leg weakness' });
  expect(within(dialog).queryByRole('group', { name: 'Who needs help' })).not.toBeInTheDocument();
});

it.each([
  ['signed out', null],
  ['locked', { crewId: 'AST-01', locked: true }],
])('works %s and lets the crew member say who needs help', async (_name, session) => {
  const api = new FakeAsclepiusApi();
  api.session = session;
  renderInWorld(<Opener />, { api, withRouter: false, signedIn: false });
  await waitFor(() => {
    expect(api.calls).toContain('auth.current');
  });
  const dialog = await openDialog();
  const who = await within(dialog).findByRole('group', { name: 'Who needs help' });
  const elena = await within(who).findByRole('button', { name: 'Elena Ruiz' });
  await userEvent.click(elena);
  expect(elena).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(within(who).getByRole('button', { name: 'Marc Dubois' }));
  expect(elena).toHaveAttribute('aria-pressed', 'false');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Leg weakness' }));
  expect(within(dialog).getByRole('button', { name: 'Send' })).toBeEnabled();
});

it('keeps Tab and Shift+Tab inside the dialog', async () => {
  renderInWorld(<Opener />, { withRouter: false });
  const dialog = await openDialog();
  const buttons = within(dialog).getAllByRole('button');
  const last = buttons[buttons.length - 1] as HTMLElement;
  act(() => {
    last.focus();
  });
  await userEvent.tab();
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  expect(document.activeElement).toBe(buttons[0]);
  await userEvent.tab({ shift: true });
  expect(document.activeElement).toBe(last);
});

it('closes on Escape and gives focus back to the opener; aria-modal is set', async () => {
  renderInWorld(<Opener />, { withRouter: false });
  const opener = screen.getByRole('button', { name: 'Urgent symptom' });
  const dialog = await openDialog();
  expect(dialog).toHaveAttribute('aria-modal', 'true');
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
});

it('still works when the crew list cannot be read', async () => {
  const api = new FakeAsclepiusApi();
  api.crew.list = () => Promise.reject(new Error('bridge down'));
  renderInWorld(<Opener />, { api, withRouter: false, signedIn: false });
  await waitFor(() => {
    expect(api.calls).toContain('auth.current');
  });
  const dialog = await openDialog();
  await userEvent.click(await within(dialog).findByRole('button', { name: 'Leg weakness' }));
  expect(within(dialog).queryByRole('group', { name: 'Who needs help' })).not.toBeInTheDocument();
  expect(within(dialog).getByRole('button', { name: 'Send' })).toBeEnabled();
});

it('wraps Shift+Tab from the dialog itself to the last control, and leaves Tab alone mid-list', async () => {
  renderInWorld(<Opener />, { withRouter: false });
  const dialog = await openDialog();
  await within(dialog).findByRole('button', { name: 'Leg weakness' });
  expect(dialog).toHaveFocus();
  await userEvent.tab({ shift: true });
  expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Close' }));
  await userEvent.tab({ shift: true });
  expect(document.activeElement).not.toBe(within(dialog).getByRole('button', { name: 'Close' }));
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
});

it('keeps keyboard focus inside the dialog after Send disables itself, so Escape still closes', async () => {
  renderInWorld(<Opener />, { withRouter: false });
  const dialog = await openDialog();
  await userEvent.click(await within(dialog).findByRole('button', { name: 'Leg weakness' }));
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
  const active = document.activeElement as HTMLElement;
  expect(dialog).toContainElement(active);
  expect(active).not.toBeDisabled();
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('treats an unknown ground link as no link: the report is queued, never promised as sent', async () => {
  const data = mockData();
  const real = data.getStrip.bind(data);
  data.getStrip = async () => ({ ...(await real()), groundLink: null });
  renderInWorld(<Opener />, { withRouter: false, data });
  const dialog = await openDialog();
  expect(await within(dialog).findByText(/No link to the ground\./)).toBeInTheDocument();
  expect(within(dialog).queryByText(/Link to the ground is up\./)).not.toBeInTheDocument();
});

// Real mode: nothing is saved, queued or sent, and nobody is alerted (#63). The dialog must say so before and after Send, whatever the link.
it.each([
  ['up', { state: 'up' }],
  ['down', { state: 'down', back_in_min: 12 }],
] as const)('in real mode never claims saved, queued, sent or alerted, and says Not recorded (link %s)', async (_name, link) => {
  const api = new FakeAsclepiusApi();
  api.viewData.strip = { ...(api.viewData.strip as object), ground_link: link } as never;
  renderInWorld(<Opener />, { api, withRouter: false, data: new RealDataSource(api.views, api.reps) });
  const dialog = await openDialog();
  await userEvent.click(await within(dialog).findByRole('button', { name: 'Leg weakness' }));
  expect(dialog).not.toHaveTextContent(/alerted|queue|saved on board|goes to the flight surgeon/i);
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
  const alert = await within(dialog).findByRole('alert');
  expect(alert).toHaveTextContent(/Not recorded/);
  expect(alert).toHaveTextContent(/Tell the crew medical officer now/);
  expect(dialog).not.toHaveTextContent(/alerted|queued|saved on board|goes to the flight surgeon/i);
  expect(within(dialog).queryByRole('button', { name: 'Queued' })).not.toBeInTheDocument();
  expect(within(dialog).getByRole('button', { name: 'Close' })).toBeEnabled();
});
