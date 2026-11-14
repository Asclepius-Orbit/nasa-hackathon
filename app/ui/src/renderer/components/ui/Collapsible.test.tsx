import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { Collapsible } from './Collapsible';

it('is closed by default and opens from its summary', async () => {
  render(<Collapsible summary="What green means">Inside your reference.</Collapsible>);
  const details = screen.getByText('What green means').closest('details');
  expect(details).not.toHaveAttribute('open');
  await userEvent.click(screen.getByText('What green means'));
  expect(details).toHaveAttribute('open');
});

it('can start open and can carry the urgent tone and an icon', () => {
  render(
    <Collapsible summary="Warning symptoms" tone="urgent" icon="!" defaultOpen>
      body
    </Collapsible>,
  );
  const details = screen.getByText('Warning symptoms').closest('details');
  expect(details).toHaveAttribute('open');
  expect(details).toHaveAttribute('data-tone', 'urgent');
  expect(screen.getByText('!')).toHaveAttribute('aria-hidden', 'true');
});

it('puts its summary in the tab order (the browser toggles it on Enter or Space)', async () => {
  render(<Collapsible summary="More">hidden</Collapsible>);
  await userEvent.tab();
  expect(screen.getByText('More').closest('summary')).toHaveFocus();
});
