import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

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
  expect(await screen.findByRole('status')).toHaveTextContent('Questionnaire saved.');
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

const realSetup = async (): Promise<FakeAsclepiusApi> => {
  const api = new FakeAsclepiusApi();
  renderInWorld(<OswestryView />, { api, data: new RealDataSource(api.views, api.reps, api), route: '/oswestry' });
  await screen.findByRole('radiogroup', { name: 'Pain intensity' });
  return api;
};

const answerAll = async (): Promise<void> => {
  for (const group of screen.getAllByRole('radiogroup')) {
    await userEvent.click(within(group).getAllByRole('radio')[2] as HTMLElement);
  }
};

describe('with the backend (real mode)', () => {
  it('serves the same provisional items, still tagged pending validation', async () => {
    await realSetup();
    expect(screen.getAllByRole('radiogroup')).toHaveLength(6);
    expect(screen.getByText('Item set pending validation by Aguado')).toBeInTheDocument();
  });

  it('saves each answer as item id and option index', async () => {
    const api = await realSetup();
    await userEvent.click(within(screen.getByRole('radiogroup', { name: 'Pain intensity' })).getByRole('radio', { name: '4. Very severe' }));
    for (const group of screen.getAllByRole('radiogroup')) {
      if (group.getAttribute('aria-label') === 'Pain intensity') continue;
      await userEvent.click(within(group).getAllByRole('radio')[0] as HTMLElement);
    }
    await userEvent.click(screen.getByRole('button', { name: 'Save questionnaire' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Questionnaire saved.');
    expect(api.lastArg['reports.saveOswestry']).toEqual({
      items: [
        { item_id: 'pain_intensity', score: 4 },
        { item_id: 'personal_care', score: 0 },
        { item_id: 'lifting', score: 0 },
        { item_id: 'sleeping', score: 0 },
        { item_id: 'working', score: 0 },
        { item_id: 'exercise_countermeasures', score: 0 },
      ],
    });
  });

  it('is busy while saving and says saved only after the backend took it', async () => {
    const api = await realSetup();
    let release: () => void = () => undefined;
    api.reports.saveOswestry = () =>
      new Promise((resolve) => {
        release = () => {
          resolve({ ok: true, value: { reportId: 'r', assessmentLevel: null, assessmentSkipped: null, exercise: null } });
        };
      });
    await answerAll();
    await userEvent.click(screen.getByRole('button', { name: 'Save questionnaire' }));
    expect(screen.getByRole('button', { name: 'Saving' })).toBeDisabled();
    expect(within(screen.getByRole('radiogroup', { name: 'Pain intensity' })).getAllByRole('radio')[1]).toBeDisabled();
    expect(screen.queryByText('Questionnaire saved.')).not.toBeInTheDocument();
    act(() => {
      release();
    });
    expect(await screen.findByText('Questionnaire saved.')).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole('radiogroup', { name: 'Pain intensity' })).getAllByRole('radio')[1] as HTMLElement);
    expect(screen.queryByText('Questionnaire saved.')).not.toBeInTheDocument();
  });

  it('a refusal lists the backend messages and does not say saved; the answers stay', async () => {
    const api = await realSetup();
    api.forced.set('reports.saveOswestry', { ok: false, reason: 'invalid', fields: [{ pointer: '/items/1/score', message: 'must be at most 5' }] });
    await answerAll();
    await userEvent.click(screen.getByRole('button', { name: 'Save questionnaire' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('/items/1/score: must be at most 5');
    expect(screen.queryByText('Questionnaire saved.')).not.toBeInTheDocument();
    expect(within(screen.getByRole('radiogroup', { name: 'Pain intensity' })).getAllByRole('radio')[2]).toBeChecked();
  });

  it.each([
    ['unavailable', 'The monitor did not answer. Nothing was saved; your answers are still here, try again.'],
    ['locked', 'The app is locked. Unlock it, then save again.'],
    ['conflict', 'The monitor already holds a different questionnaire for this week. Nothing was saved; reload the screen.'],
  ] as const)('on %s says nothing was saved', async (reason, text) => {
    const api = await realSetup();
    api.forced.set('reports.saveOswestry', { ok: false, reason });
    await answerAll();
    await userEvent.click(screen.getByRole('button', { name: 'Save questionnaire' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
    expect(screen.queryByText('Questionnaire saved.')).not.toBeInTheDocument();
  });

  it('says the monitor did not answer when the bridge itself fails', async () => {
    const api = await realSetup();
    api.rejecting.add('reports.saveOswestry');
    await answerAll();
    await userEvent.click(screen.getByRole('button', { name: 'Save questionnaire' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was saved');
  });
});
