import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { renderInWorld } from '../../test/providers';
import { describePick, SpineFigure } from './SpineFigure';

it('names a vertebra when it is clicked and clears on a second click or Escape', () => {
  const { container } = renderInWorld(<SpineFigure lumbarLengthMm={1} />);
  expect(container.querySelector('[aria-live] strong')).toBeNull();
  const t9 = container.querySelector('[data-pick="T9"]');
  if (t9 === null) throw new Error('T9 not drawn');
  fireEvent.click(t9);
  expect(screen.getByText('T9: Ninth thoracic vertebra')).toBeInTheDocument();
  expect(screen.getByText(/The IMU sits here/)).toBeInTheDocument();
  fireEvent.click(t9);
  expect(container.querySelector('[aria-live] strong')).toBeNull();
  fireEvent.click(container.querySelector('[data-pick="L4"]') as Element);
  expect(screen.getByText(/ground electrode/)).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole('toolbar'), { key: 'Escape' });
  expect(container.querySelector('[aria-live] strong')).toBeNull();
});

it('lets the keyboard pick a sensor through the toolbar and shows a tag on hover', async () => {
  const { container } = renderInWorld(<SpineFigure lumbarLengthMm={null} />);
  await userEvent.click(screen.getByRole('button', { name: 'IMU' }));
  expect(screen.getByText(/Inertial sensor taped over T9/)).toBeInTheDocument();
  fireEvent.pointerEnter(container.querySelector('[data-pick="L2"]') as Element);
  expect(container.querySelector('text')?.parentElement).not.toBeNull();
  expect(describePick('cord').title).toBe('Stretch cord');
});
