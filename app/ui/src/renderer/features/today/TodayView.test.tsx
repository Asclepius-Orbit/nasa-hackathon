import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import { TODAY } from '../../data/mock/mock-data';
import type { TodayData } from '../../data/types';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { TodayView } from './TodayView';

function Where(): React.JSX.Element {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

const setup = (options: Parameters<typeof renderInWorld>[1] = {}): ReturnType<typeof renderInWorld> =>
  renderInWorld(
    <>
      <TodayView />
      <Where />
    </>,
    { route: '/today', ...options },
  );

class WithToday extends MockDataSource {
  constructor(private readonly today: TodayData) {
    super();
  }
  override getToday(): ReturnType<MockDataSource['getToday']> {
    return Promise.resolve(this.today);
  }
}

it('shows the verdict: level word, meaning, what to do and the plan', async () => {
  setup();
  expect(await screen.findByRole('heading', { level: 1, name: 'Amber' })).toBeInTheDocument();
  expect(screen.getByText(/Amber means a measured change from your own preflight reference/)).toBeInTheDocument();
  expect(screen.getByText('What to do today:').parentElement).toHaveTextContent('What to do today: the two plan items below, and your daily report.');
  const plan = screen.getByRole('list', { name: 'Plan for today' });
  expect(within(plan).getAllByRole('listitem')).toHaveLength(2);
  expect(within(plan).getByText('Lumbar stabilisation exercises, 10 min')).toBeInTheDocument();
  expect(within(plan).getByRole('button', { name: 'Open procedure' })).toBeDisabled();
});

it('composes the session-in-progress line from the live rep count and the return button navigates', async () => {
  const { scheduler } = setup();
  const item = (await screen.findByText('Repeat the standing lat pulldown session today')).closest('li');
  expect(item).not.toBeNull();
  await screen.findByText(/In progress since 13:38 GMT, rep 4 of 10. Recommendation REC_SESSION_REPEAT_01./);
  act(() => {
    scheduler.advance(4500);
  });
  expect(item).toHaveTextContent('rep 5 of 10');
  await userEvent.click(within(item as HTMLElement).getByRole('button', { name: 'Return to session' }));
  expect(screen.getByTestId('where')).toHaveTextContent('/session');
});

it('has no in-progress line when no session is running', async () => {
  const today: TodayData = { ...TODAY, sessionInProgress: null };
  setup({ data: new WithToday(today) });
  const item = (await screen.findByText('Repeat the standing lat pulldown session today')).closest('li');
  expect(item).not.toHaveTextContent('In progress');
  expect(item).toHaveTextContent('Recommendation REC_SESSION_REPEAT_01.');
});

it('draws the spine figure from the data: lumbar length in the chosen unit, sensors named', async () => {
  const api = new FakeAsclepiusApi();
  api.settingsValue = { ...api.settingsValue, lengthUnit: 'in' };
  setup({ api });
  const figure = (await screen.findByRole('img', { name: /Side view of the spine.*\+0\.08 in/ }));
  expect(figure).toHaveAccessibleName(/\+0\.08 in/);
  expect(within(figure).getByText('+0.08 in')).toBeInTheDocument();
  for (const text of ['EMG', 'IMU', 'Stretch cord', 'Preflight', 'Today']) {
    expect(within(figure).getByText(text)).toBeInTheDocument();
  }
  expect(screen.getByText(/The cord measures length from L1 to L5/)).toBeInTheDocument();
});

it('moves the lumbar vertebrae apart as the lumbar length grows', async () => {
  const gap = async (mm: number): Promise<number> => {
    const { container, unmount } = setup({ data: new WithToday({ ...TODAY, lumbarLengthMm: mm }) });
    await screen.findByRole('img', { name: /Side view of the spine/ });
    const ys = [...container.querySelectorAll('[data-vertebra^="L"]')].map((e) => Number(e.getAttribute('cy')));
    unmount();
    return (ys.at(-1) ?? 0) - (ys[0] ?? 0);
  };
  const short = await gap(-2);
  const long = await gap(4);
  expect(long).toBeGreaterThan(short);
});

it('lists the warning symptoms from the catalogue and opens the Urgent dialog', async () => {
  setup();
  const toggle = await screen.findByText('Warning symptoms: the 8 that mean press Urgent symptom');
  const details = toggle.closest('details') as HTMLElement;
  await screen.findByText('Leg weakness');
  expect(within(details).getAllByRole('listitem', { hidden: true })).toHaveLength(8);
  await userEvent.click(within(details).getByRole('button', { name: 'Urgent symptom', hidden: true }));
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
});

it('explains green, amber and red', async () => {
  setup();
  const details = (await screen.findByText('What green, amber and red mean')).closest('details') as HTMLElement;
  expect(within(details).getByText('Inside your reference. Keep your usual routine and daily report.')).toBeInTheDocument();
  expect(within(details).getByText(/The crew medical officer and the flight surgeon are alerted/)).toBeInTheDocument();
});

it('says why amber and how far to trust it', async () => {
  setup();
  expect(await screen.findByRole('heading', { name: 'Why amber' })).toBeInTheDocument();
  expect(screen.getByText('Lumbar length')).toBeInTheDocument();
  expect(screen.getByText(/2\.1 mm above your preflight reference in 1 g\./)).toBeInTheDocument();
  expect(screen.getByText('Confidence').parentElement).toHaveTextContent('70 %');
  expect(screen.getByText('Data available').parentElement).toHaveTextContent('8 of 10');
  expect(screen.getByText(/Missing: right latissimus EMG/)).toBeInTheDocument();
  expect(screen.getByText('Assessed').parentElement).toHaveTextContent('42 min ago');
});

it('does not invent a missing-data line when everything is available', async () => {
  const today: TodayData = { ...TODAY, trust: { ...TODAY.trust, dataAvailable: 10, missing: '' } };
  setup({ data: new WithToday(today) });
  await screen.findByRole('heading', { name: 'Why amber' });
  expect(screen.queryByText(/Missing:/)).not.toBeInTheDocument();
});

it('lists what else to do today; the buttons navigate', async () => {
  setup();
  expect(await screen.findByRole('heading', { level: 2, name: 'Also today' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Answer now' }));
  expect(screen.getByTestId('where')).toHaveTextContent('/oswestry');
});

it('uses the level word of the data for a red day', async () => {
  setup({ data: new WithToday({ ...TODAY, level: 'red' }) });
  expect(await screen.findByRole('heading', { level: 1, name: 'Red' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Why red' })).toBeInTheDocument();
});

it('says so when today cannot be loaded', async () => {
  class Failing extends MockDataSource {
    override getToday(): ReturnType<MockDataSource['getToday']> {
      return Promise.reject(new Error('down'));
    }
  }
  setup({ data: new Failing() });
  expect(await screen.findByRole('alert')).toHaveTextContent('Today could not be loaded.');
});
