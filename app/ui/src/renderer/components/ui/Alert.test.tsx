import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { Alert } from './Alert';

it('is an alert by default, with title, body and action', () => {
  render(
    <Alert title="1 warning symptom marked." action={<button type="button">Send alert</button>}>
      Leg weakness.
    </Alert>,
  );
  const alert = screen.getByRole('alert');
  expect(alert).toHaveAttribute('data-variant', 'urgent');
  expect(alert).toHaveTextContent('1 warning symptom marked.');
  expect(alert).toHaveTextContent('Leg weakness.');
  expect(screen.getByRole('button', { name: 'Send alert' })).toBeInTheDocument();
});

it('has calmer variants, and can be a polite status instead', () => {
  render(
    <Alert variant="warning" role="status">
      The app cannot reach its data service.
    </Alert>,
  );
  expect(screen.getByRole('status')).toHaveAttribute('data-variant', 'warning');
  render(<Alert variant="info">Queued.</Alert>);
  expect(screen.getByText('Queued.').closest('[role="alert"]')).toHaveAttribute('data-variant', 'info');
});
