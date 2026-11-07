import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { renderInWorld } from '../../test/providers';
import { ReportView } from './ReportView';

const PAIN = 'How much does your back hurt right now?';
const STIFF = 'How stiff does your back feel?';
const FLAGS = 'Do you have any of these now?';

const setup = async (): Promise<void> => {
  renderInWorld(<ReportView />, { route: '/report' });
  await screen.findByRole('radiogroup', { name: PAIN });
};

const choose = async (group: string, n: string): Promise<void> => {
  await userEvent.click(within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name: n }));
};

const save = (): HTMLElement => screen.getByRole('button', { name: 'Save daily report' });

it('keeps Save disabled and names what is still unanswered', async () => {
  await setup();
  expect(save()).toBeDisabled();
  expect(screen.getByText('Still to answer: pain, stiffness, warning symptoms.')).toBeInTheDocument();
  await choose(PAIN, '3');
  await choose(STIFF, '2');
  expect(save()).toBeDisabled();
  expect(screen.getByText('Still to answer: warning symptoms.')).toBeInTheDocument();
});

it('accepts an explicit "None of these" as the warning-symptom answer and then enables Save', async () => {
  await setup();
  await choose(PAIN, '0');
  await choose(STIFF, '0');
  await userEvent.click(screen.getByRole('button', { name: 'None of these' }));
  expect(save()).toBeEnabled();
  expect(screen.getByText('Ready. Saving runs a new assessment.')).toBeInTheDocument();
});

it('"None of these" and a symptom exclude each other', async () => {
  await setup();
  const group = screen.getByRole('group', { name: FLAGS });
  await userEvent.click(within(group).getByRole('button', { name: 'None of these' }));
  await userEvent.click(within(group).getByRole('button', { name: 'Leg weakness' }));
  expect(within(group).getByRole('button', { name: 'None of these' })).toHaveAttribute('aria-pressed', 'false');
  await userEvent.click(within(group).getByRole('button', { name: 'None of these' }));
  expect(within(group).getByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'false');
});

it('shows the where-does-it-hurt grid only when pain is above zero and toggles cells', async () => {
  await setup();
  expect(screen.queryByRole('button', { name: 'Lower back, left' })).not.toBeInTheDocument();
  await choose(PAIN, '0');
  expect(screen.queryByRole('button', { name: 'Lower back, left' })).not.toBeInTheDocument();
  await choose(PAIN, '4');
  const cell = screen.getByRole('button', { name: 'Lower back, left' });
  expect(screen.getAllByRole('button', { name: /^(Neck|Upper back|Lower back|Tailbone), / })).toHaveLength(16);
  await userEvent.click(cell);
  expect(cell).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(cell);
  expect(cell).toHaveAttribute('aria-pressed', 'false');
});

it('opens a live alert for marked symptoms and "Send alert" opens Urgent with them marked', async () => {
  await setup();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Leg weakness' }));
  await userEvent.click(screen.getByRole('button', { name: 'Fever with back pain' }));
  const alert = screen.getByRole('alert');
  expect(within(alert).getByText('2 warning symptoms marked.')).toBeInTheDocument();
  expect(alert).toHaveTextContent('Leg weakness; Fever with back pain.');
  await userEvent.click(within(alert).getByRole('button', { name: 'Send alert' }));
  const dialog = await screen.findByRole('dialog');
  expect(await within(dialog).findByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'true');
  expect(within(dialog).getByRole('button', { name: 'Fever with back pain' })).toHaveAttribute('aria-pressed', 'true');
  expect(within(dialog).getByRole('button', { name: 'Trouble controlling or passing urine or stool' })).toHaveAttribute('aria-pressed', 'false');
});

it('says "1 warning symptom marked."', async () => {
  await setup();
  await userEvent.click(screen.getByRole('button', { name: 'Leg weakness' }));
  expect(screen.getByText('1 warning symptom marked.')).toBeInTheDocument();
});

it('offers optional sleep and exercise choices, one at a time', async () => {
  await setup();
  const sleep = screen.getByRole('group', { name: 'How long did you sleep?' });
  await userEvent.click(within(sleep).getByRole('button', { name: '6 h' }));
  await userEvent.click(within(sleep).getByRole('button', { name: 'Not sure' }));
  expect(within(sleep).getByRole('button', { name: '6 h' })).toHaveAttribute('aria-pressed', 'false');
  expect(within(sleep).getByRole('button', { name: 'Not sure' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('radiogroup', { name: 'How well did you sleep?' })).toBeInTheDocument();
  const kind = screen.getByRole('group', { name: 'Exercise device' });
  await userEvent.click(within(kind).getByRole('button', { name: 'CEVIS' }));
  expect(within(kind).getByRole('button', { name: 'CEVIS' })).toHaveAttribute('aria-pressed', 'true');
  const minutes = screen.getByRole('group', { name: 'Exercise duration' });
  await userEvent.click(within(minutes).getByRole('button', { name: '45 min' }));
  expect(within(minutes).getByRole('button', { name: '45 min' })).toHaveAttribute('aria-pressed', 'true');
});

it('shows a confirmation after saving', async () => {
  await setup();
  await choose(PAIN, '2');
  await choose(STIFF, '5');
  await userEvent.click(screen.getByRole('button', { name: 'None of these' }));
  await userEvent.click(save());
  expect(screen.getByRole('status')).toHaveTextContent('Daily report saved. A new assessment is running.');
});

it('clears an optional choice when it is pressed again', async () => {
  await setup();
  const sleep = screen.getByRole('group', { name: 'How long did you sleep?' });
  await userEvent.click(within(sleep).getByRole('button', { name: '7 h' }));
  await userEvent.click(within(sleep).getByRole('button', { name: '7 h' }));
  expect(within(sleep).getByRole('button', { name: '7 h' })).toHaveAttribute('aria-pressed', 'false');
});
