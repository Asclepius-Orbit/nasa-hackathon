import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { Keypad } from './Keypad';

it('has digits 0 to 9, Delete and Enter', () => {
  render(<Keypad onDigit={() => undefined} onDelete={() => undefined} onEnter={() => undefined} />);
  for (const d of '0123456789') expect(screen.getByRole('button', { name: d })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Delete last digit' })).toHaveTextContent('Delete');
  expect(screen.getByRole('button', { name: 'Enter' })).toBeInTheDocument();
});

it('reports each key', async () => {
  const onDigit = vi.fn();
  const onDelete = vi.fn();
  const onEnter = vi.fn();
  render(<Keypad onDigit={onDigit} onDelete={onDelete} onEnter={onEnter} />);
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  await userEvent.click(screen.getByRole('button', { name: '0' }));
  await userEvent.click(screen.getByRole('button', { name: 'Delete last digit' }));
  await userEvent.click(screen.getByRole('button', { name: 'Enter' }));
  expect(onDigit.mock.calls).toEqual([['7'], ['0']]);
  expect(onDelete).toHaveBeenCalledOnce();
  expect(onEnter).toHaveBeenCalledOnce();
});

it('can be disabled as a whole, e.g. while waiting for a name', async () => {
  const onDigit = vi.fn();
  render(<Keypad onDigit={onDigit} onDelete={() => undefined} onEnter={() => undefined} disabled />);
  await userEvent.click(screen.getByRole('button', { name: '5' }));
  expect(onDigit).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Enter' })).toBeDisabled();
});
