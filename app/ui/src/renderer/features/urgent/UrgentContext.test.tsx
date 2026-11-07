import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { UrgentProvider, useUrgent } from './UrgentContext';
import { UrgentDialog } from './UrgentDialog';

function Opener(): React.JSX.Element {
  const { open, isOpen } = useUrgent();
  return (
    <button
      type="button"
      onClick={() => {
        open();
      }}
    >
      {isOpen ? 'is open' : 'is closed'}
    </button>
  );
}

const setup = (): void => {
  render(
    <UrgentProvider>
      <Opener />
      <UrgentDialog />
    </UrgentProvider>,
  );
};

it('opens and closes the dialog from anywhere, with no session needed', async () => {
  setup();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'is closed' }));
  expect(screen.getByRole('dialog', { name: 'What do you have now?' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('closes on Escape and returns focus to what opened it', async () => {
  setup();
  await userEvent.click(screen.getByRole('button', { name: 'is closed' }));
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'is closed' })).toHaveFocus();
});

it('useUrgent outside a provider fails loudly', () => {
  expect(() => render(<Opener />)).toThrow(/UrgentProvider/);
});

it('remembers symptoms preselected by the caller and forgets them on a plain open', async () => {
  function Reader(): React.JSX.Element {
    const { open, preselected } = useUrgent();
    return (
      <>
        <button
          type="button"
          onClick={() => {
            open(['leg_weakness']);
          }}
        >
          with
        </button>
        <button
          type="button"
          onClick={() => {
            open();
          }}
        >
          without
        </button>
        <p data-testid="pre">{preselected.join(',')}</p>
      </>
    );
  }
  render(
    <UrgentProvider>
      <Reader />
    </UrgentProvider>,
  );
  await userEvent.click(screen.getByRole('button', { name: 'with' }));
  expect(screen.getByTestId('pre')).toHaveTextContent('leg_weakness');
  await userEvent.click(screen.getByRole('button', { name: 'without' }));
  expect(screen.getByTestId('pre')).toBeEmptyDOMElement();
});

it('ignores a click event when open is used directly as an onClick handler', async () => {
  function Direct(): React.JSX.Element {
    const { open, preselected } = useUrgent();
    return (
      <>
        <button type="button" onClick={open as unknown as React.MouseEventHandler}>
          direct
        </button>
        <p data-testid="count">{preselected.length}</p>
      </>
    );
  }
  render(
    <UrgentProvider>
      <Direct />
    </UrgentProvider>,
  );
  await userEvent.click(screen.getByRole('button', { name: 'direct' }));
  expect(screen.getByTestId('count')).toHaveTextContent('0');
});
