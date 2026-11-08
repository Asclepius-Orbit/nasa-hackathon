import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { RealDataSource } from '../../data/real/RealDataSource';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { OswestryView } from './OswestryView';

const setup = async (): Promise<void> => {
  renderInWorld(<OswestryView />, { route: '/oswestry' });
  await screen.findByRole('radiogroup', { name: 'Pain intensity' });
};

it('shows six items of six numbered options and the pending-validation note', async () => {
  await setup();
  expect(screen.getAllByRole('radiogroup')).toHaveLength(6);
  const group = screen.getByRole('radiogroup', { name: 'Sleeping' });
  expect(within(group).getAllByRole('radio')).toHaveLength(6);
  expect(within(group).getByRole('radio', { name: '3. Under 4 h' })).toBeInTheDocument();
  expect(screen.getByText('Item set pending validation by Aguado')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save questionnaire' })).toBeDisabled();
  expect(screen.getByText('6 of 6 still to answer.')).toBeInTheDocument();
});

it('counts down while answering and enables Save when complete, then confirms', async () => {
  await setup();
  const titles = ['Pain intensity', 'Personal care', 'Lifting and pulling', 'Sleeping', 'Working', 'Exercise and countermeasures'];
  for (const [i, title] of titles.entries()) {
    await userEvent.click(within(screen.getByRole('radiogroup', { name: title })).getAllByRole('radio')[i % 6] as HTMLElement);
    if (i < 5) expect(screen.getByText(`${String(5 - i)} of 6 still to answer.`)).toBeInTheDocument();
  }
  expect(screen.getByText('Ready to save.')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Save questionnaire' }));
  expect(screen.getByRole('status')).toHaveTextContent('Questionnaire saved.');
});

it('allows changing an answer (one option per item)', async () => {
  await setup();
  const group = screen.getByRole('radiogroup', { name: 'Pain intensity' });
  await userEvent.click(within(group).getByRole('radio', { name: '1. Very mild' }));
  await userEvent.click(within(group).getByRole('radio', { name: '4. Very severe' }));
  expect(within(group).getByRole('radio', { name: '1. Very mild' })).not.toBeChecked();
  expect(within(group).getByRole('radio', { name: '4. Very severe' })).toBeChecked();
  expect(screen.getByText('5 of 6 still to answer.')).toBeInTheDocument();
});

it('with the backend (real mode) says it is not available and offers no questionnaire to save', async () => {
  const api = new FakeAsclepiusApi();
  renderInWorld(<OswestryView />, { api, data: new RealDataSource(api.views), route: '/oswestry' });
  expect(await screen.findByTestId('not-built')).toBeInTheDocument();
  expect(screen.getByText('Not available yet.')).toBeInTheDocument();
  expect(screen.queryByText(/did not answer/)).not.toBeInTheDocument();
  expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Save questionnaire' })).not.toBeInTheDocument();
});
