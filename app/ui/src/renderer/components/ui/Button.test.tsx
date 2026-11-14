import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { Button } from './Button';

it('is a real button that does not submit forms by default', () => {
  render(<Button>Save</Button>);
  expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('type', 'button');
});

it.each(['primary', 'quiet', 'urgent'] as const)('marks the %s variant for styling', (variant) => {
  render(<Button variant={variant}>x</Button>);
  expect(screen.getByRole('button')).toHaveAttribute('data-variant', variant);
});

it('defaults to primary', () => {
  render(<Button>x</Button>);
  expect(screen.getByRole('button')).toHaveAttribute('data-variant', 'primary');
});

it('calls onClick, and not when disabled', async () => {
  const onClick = vi.fn();
  const { rerender } = render(<Button onClick={onClick}>Go</Button>);
  await userEvent.click(screen.getByRole('button'));
  expect(onClick).toHaveBeenCalledOnce();
  rerender(
    <Button onClick={onClick} disabled>
      Go
    </Button>,
  );
  await userEvent.click(screen.getByRole('button'));
  expect(onClick).toHaveBeenCalledOnce();
});
