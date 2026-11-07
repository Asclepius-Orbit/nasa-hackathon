import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';

import { Scale } from './Scale';

function Harness({ initial = null }: { initial?: number | null }): React.JSX.Element {
  const [value, setValue] = useState<number | null>(initial);
  return <Scale label="How much does your back hurt right now?" value={value} onChange={setValue} lowLabel="0, no pain" highLabel="10, worst imaginable" />;
}

it('offers 0 to 10 as one named radio group with end labels', () => {
  render(<Harness />);
  const group = screen.getByRole('radiogroup', { name: 'How much does your back hurt right now?' });
  expect(group).toBeInTheDocument();
  expect(screen.getAllByRole('radio')).toHaveLength(11);
  expect(screen.getByText('0, no pain')).toBeInTheDocument();
  expect(screen.getByText('10, worst imaginable')).toBeInTheDocument();
});

it('starts with nothing chosen: no answer is not zero', () => {
  render(<Harness />);
  for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked();
});

it('reports the chosen number on click', async () => {
  const onChange = vi.fn();
  render(<Scale label="Pain" value={null} onChange={onChange} />);
  await userEvent.click(screen.getByRole('radio', { name: '7' }));
  expect(onChange).toHaveBeenCalledWith(7);
});

it('moves with the arrow keys and shows the choice', async () => {
  render(<Harness initial={3} />);
  expect(screen.getByRole('radio', { name: '3' })).toBeChecked();
  await userEvent.tab();
  expect(screen.getByRole('radio', { name: '3' })).toHaveFocus();
  await userEvent.keyboard('{ArrowRight}');
  expect(screen.getByRole('radio', { name: '4' })).toBeChecked();
});

it('can be a shorter scale', () => {
  render(<Scale label="Pick" value={null} onChange={() => undefined} min={0} max={5} />);
  expect(screen.getAllByRole('radio')).toHaveLength(6);
});
