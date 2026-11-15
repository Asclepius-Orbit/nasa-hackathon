import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { InfoTip } from './InfoTip';

it('renders a closed trigger', () => {
  render(<InfoTip label="About x">Help text</InfoTip>);
  expect(screen.getByRole('button', { name: 'About x' })).toBeInTheDocument();
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('shows on hover and focus, linked by aria-describedby', async () => {
  const user = userEvent.setup();
  render(<InfoTip label="About x">Help text</InfoTip>);
  const button = screen.getByRole('button', { name: 'About x' });
  await user.hover(button);
  expect(screen.getByRole('tooltip')).toHaveTextContent('Help text');
  expect(button).toHaveAttribute('aria-describedby', screen.getByRole('tooltip').id);
  await user.unhover(button);
  expect(screen.queryByRole('tooltip')).toBeNull();
  await user.tab();
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

it('Escape hides it, and Enter pins and unpins it', async () => {
  const user = userEvent.setup();
  render(<InfoTip label="About x">Help text</InfoTip>);
  await user.tab();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('tooltip')).toBeNull();
  await user.keyboard('{Enter}');
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await user.keyboard('{Enter}');
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('closes on a click outside', async () => {
  const user = userEvent.setup();
  render(<InfoTip label="About x">Help text</InfoTip>);
  await user.click(screen.getByRole('button', { name: 'About x' }));
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('tooltip')).toBeNull();
});
