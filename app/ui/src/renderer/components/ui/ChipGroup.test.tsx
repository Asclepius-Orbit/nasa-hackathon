import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, it } from 'vitest';

import { ChipGroup } from './ChipGroup';

const OPTIONS = [
  { value: 'leg_weakness', label: 'Leg weakness' },
  { value: 'trauma_event', label: 'A knock, fall or strain' },
] as const;

function Harness({ initial = [] }: { initial?: string[] }): React.JSX.Element {
  const [selected, setSelected] = useState<string[]>(initial);
  return <ChipGroup label="Do you have any of these now?" options={OPTIONS} selected={selected} onChange={setSelected} />;
}

it('is a named group of toggle buttons, none pressed at first', () => {
  render(<Harness />);
  const group = screen.getByRole('group', { name: 'Do you have any of these now?' });
  expect(group).toBeInTheDocument();
  for (const chip of screen.getAllByRole('button')) expect(chip).toHaveAttribute('aria-pressed', 'false');
});

it('toggles several chips on and off', async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole('button', { name: 'Leg weakness' }));
  await userEvent.click(screen.getByRole('button', { name: 'A knock, fall or strain' }));
  expect(screen.getByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'A knock, fall or strain' })).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(screen.getByRole('button', { name: 'Leg weakness' }));
  expect(screen.getByRole('button', { name: 'Leg weakness' })).toHaveAttribute('aria-pressed', 'false');
});

it('shows initially selected values as pressed', () => {
  render(<Harness initial={['trauma_event']} />);
  expect(screen.getByRole('button', { name: 'A knock, fall or strain' })).toHaveAttribute('aria-pressed', 'true');
});

it('marks the flag tone for warning symptoms', () => {
  render(<ChipGroup label="Flags" tone="flag" options={OPTIONS} selected={[]} onChange={() => undefined} />);
  expect(screen.getByRole('group', { name: 'Flags' })).toHaveAttribute('data-tone', 'flag');
});
