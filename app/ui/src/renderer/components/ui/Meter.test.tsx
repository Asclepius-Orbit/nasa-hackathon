import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { Meter } from './Meter';

it('is a named progress bar with its value', () => {
  render(<Meter label="Plan progress" value={1} max={2} />);
  const bar = screen.getByRole('progressbar', { name: 'Plan progress' });
  expect(bar).toHaveAttribute('aria-valuenow', '1');
  expect(bar).toHaveAttribute('aria-valuemax', '2');
  expect(bar).toHaveAttribute('aria-valuemin', '0');
});

it('fills in proportion and never past the ends', () => {
  const { rerender } = render(<Meter label="m" value={1} max={4} />);
  expect(screen.getByTestId('meter-fill')).toHaveStyle({ width: '25%' });
  rerender(<Meter label="m" value={9} max={4} />);
  expect(screen.getByTestId('meter-fill')).toHaveStyle({ width: '100%' });
  rerender(<Meter label="m" value={-3} max={4} />);
  expect(screen.getByTestId('meter-fill')).toHaveStyle({ width: '0%' });
});

it('an empty scale reads as zero, not as full', () => {
  render(<Meter label="m" value={0} max={0} />);
  expect(screen.getByTestId('meter-fill')).toHaveStyle({ width: '0%' });
});
