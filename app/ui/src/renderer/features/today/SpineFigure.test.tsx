import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { renderInWorld } from '../../test/providers';
import { SpineFigure } from './SpineFigure';

const setup = (): ReturnType<typeof renderInWorld> => renderInWorld(<SpineFigure lumbarLengthMm={2} />);

it('labels every vertebra from C1 to the coccyx, the sacrum and the coccyx', () => {
  const { container } = setup();
  const names = [...container.querySelectorAll('[data-vertebra]')].map((e) => e.getAttribute('data-vertebra'));
  expect(names).toEqual(['C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8', 'T9', 'T10', 'T11', 'T12', 'L1', 'L2', 'L3', 'L4', 'L5', 'Sacrum', 'Coccyx']);
  expect(screen.getByLabelText(/^C2 cervical vertebra\. The axis/)).toBeInTheDocument();
  expect(screen.getByLabelText(/^L3 lumbar vertebra\./)).toHaveAttribute('tabindex', '0');
  expect(screen.getByLabelText(/^Coccyx\. The tailbone/)).toBeInTheDocument();
});

it('shows one tooltip at a time on hover and hides it on leave', async () => {
  const user = userEvent.setup();
  setup();
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await user.hover(screen.getByLabelText(/^L5 lumbar vertebra/));
  expect(screen.getByRole('tooltip')).toHaveTextContent(/L5.*lowest and largest/);
  await user.hover(screen.getByLabelText(/^Sacrum\./));
  expect(screen.getAllByRole('tooltip')).toHaveLength(1);
  expect(screen.getByRole('tooltip')).toHaveTextContent(/Five fused vertebrae/);
  await user.unhover(screen.getByLabelText(/^Sacrum\./));
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

it('shows the same tooltip on keyboard focus and closes it with Escape', async () => {
  const user = userEvent.setup();
  setup();
  await user.tab();
  expect(screen.getByLabelText(/^C1 cervical vertebra/)).toHaveFocus();
  expect(screen.getByRole('tooltip')).toHaveTextContent(/C1.*atlas/);
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await user.tab();
  expect(screen.getByLabelText(/^C2 cervical vertebra/)).toHaveFocus();
});
