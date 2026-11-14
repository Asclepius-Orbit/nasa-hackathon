import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { Stepper } from './Stepper';

const STEPS = [
  { id: 'fit', label: 'Fit electrodes, IMU and cord', status: 'done' },
  { id: 'reps', label: 'Do the reps', status: 'now' },
  { id: 'finish', label: 'Rest and finish', status: 'todo' },
] as const;

it('is a named ordered list of steps', () => {
  render(<Stepper label="Protocol steps" steps={STEPS} />);
  const list = screen.getByRole('list', { name: 'Protocol steps' });
  expect(list.tagName).toBe('OL');
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
});

it('marks the current step for assistive tech and the status for styling', () => {
  render(<Stepper label="Steps" steps={STEPS} onSelect={() => undefined} />);
  expect(screen.getByRole('button', { name: 'Do the reps' })).toHaveAttribute('aria-current', 'step');
  expect(screen.getByRole('button', { name: 'Fit electrodes, IMU and cord' })).not.toHaveAttribute('aria-current');
  expect(screen.getByText('Rest and finish').closest('li')).toHaveAttribute('data-status', 'todo');
});

it('lets the user look at another step', async () => {
  const onSelect = vi.fn();
  render(<Stepper label="Steps" steps={STEPS} viewing="fit" onSelect={onSelect} />);
  expect(screen.getByText('Fit electrodes, IMU and cord').closest('li')).toHaveAttribute('data-viewing', 'true');
  await userEvent.click(screen.getByRole('button', { name: 'Rest and finish' }));
  expect(onSelect).toHaveBeenCalledWith('finish');
});

it('is read-only without onSelect', () => {
  render(<Stepper label="Steps" steps={STEPS} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(screen.getByText('Do the reps').closest('li')).toHaveAttribute('aria-current', 'step');
});
