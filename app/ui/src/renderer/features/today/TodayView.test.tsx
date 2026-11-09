import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import { todayData } from '../../data/mock/mock-data';
import { RealDataSource } from '../../data/real/RealDataSource';
import { example } from '../../test/contract-examples';
import type { RepStateView, TodayView as TodayViewPayload } from '../../../shared/api';

const TODAY = todayData(new Date('2026-11-05T13:40:00Z'));
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
  expect(screen.getByText(/2\.4 standard deviations above your preflight reference\./)).toBeInTheDocument();
  expect(screen.getByText(/1\.5 standard deviations below your preflight reference\./)).toBeInTheDocument();
  expect(screen.getByText('Confidence').parentElement).toHaveTextContent('70 %');
  expect(screen.getByText('Data available').parentElement).toHaveTextContent('8 of 10');
  expect(
    screen.getByText("Missing: Right latissimus EMG frequency (sensor offline); Today's report (no data)"),
  ).toBeInTheDocument();
  expect(screen.getByText('Assessed').parentElement).toHaveTextContent('42 min ago');
});

it('does not invent a missing-data line when everything is available', async () => {
  const today: TodayData = { ...TODAY, trust: { ...(TODAY.trust as NonNullable<TodayData['trust']>), dataAvailable: 10, missing: [] } };
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

const realSetup = (today: TodayViewPayload | null): ReturnType<typeof renderInWorld> => {
  const api = new FakeAsclepiusApi();
  if (today) api.viewData.today = today;
  return setup({ api, data: new RealDataSource(api.views, api.reps) });
};

it('shows a real amber day from the backend example: plan from codes, an unknown code kept as it is', async () => {
  const view = example<TodayViewPayload>('ui-today');
  realSetup({ ...view, recommendations: [...view.recommendations, 'REC_FUTURE_99'] });
  expect(await screen.findByRole('heading', { level: 1, name: 'Amber' })).toBeInTheDocument();
  expect(screen.getByText('Lumbar stabilisation exercises, 10 min')).toBeInTheDocument();
  expect(screen.getByText('Add resistive exercise (ARED) time')).toBeInTheDocument();
  expect(screen.getByText('REC_FUTURE_99')).toBeInTheDocument();
  expect(screen.getByText('Confidence').parentElement).toHaveTextContent('74 %');
  expect(screen.getByRole('img', { name: /\+6\.4 mm/ })).toBeInTheDocument();
  expect(screen.getByText('Lumbar length measured 9 Feb.')).toBeInTheDocument();
});

it('shows no data explicitly: no level colour word, no confidence, no length, no plan, and says nothing is assessed', async () => {
  realSetup(example<TodayViewPayload>('ui-today.nodata'));
  expect(await screen.findByRole('heading', { level: 1, name: 'No data' })).toHaveAttribute('data-level', 'nodata');
  expect(screen.getByText(/It does not mean everything is fine/)).toBeInTheDocument();
  expect(screen.getByText(/There is no assessment yet/)).toBeInTheDocument();
  expect(screen.queryByText('Confidence')).not.toBeInTheDocument();
  expect(screen.queryByText('Assessed')).not.toBeInTheDocument();
  const figure = screen.getByRole('img', { name: /Side view of the spine. Lumbar length No data/ });
  expect(within(figure).getAllByText('No data').length).toBeGreaterThan(0);
  expect(screen.getByRole('heading', { name: 'Why no data' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start daily report' })).toBeInTheDocument();
});

it('lists a running session even when no recommendation points to it', async () => {
  realSetup(example<TodayViewPayload>('ui-today.nodata'));
  const row = (await screen.findByText('Session in progress')).closest('li') as HTMLElement;
  await userEvent.click(within(row).getByRole('button', { name: 'Return to session' }));
  expect(screen.getByTestId('where')).toHaveTextContent('/session');
});

it('says there is no connection to the monitor when the backend is down', async () => {
  realSetup(null);
  expect(await screen.findByTestId('no-monitor')).toHaveTextContent('No connection to the monitor');
  expect(screen.queryByRole('heading', { level: 1, name: 'Amber' })).not.toBeInTheDocument();
});

it('on a locked bridge it does not claim the monitor is down: it re-reads the session and the lock flow takes over', async () => {
  const api = new FakeAsclepiusApi();
  api.views.today = () => {
    api.session = { crewId: 'AST-01', locked: true };
    return Promise.reject(new Error('locked'));
  };
  setup({ api, data: new RealDataSource(api.views, api.reps) });
  await screen.findByRole('status');
  await waitFor(() => {
    expect(api.calls.filter((c) => c === 'auth.current').length).toBeGreaterThan(1);
  });
  expect(screen.queryByTestId('no-monitor')).not.toBeInTheDocument();
});

it('with the backend it never shows a simulated rep count: only the title, start and total from the contract', async () => {
  const { scheduler } = realSetup(example<TodayViewPayload>('ui-today.nodata'));
  const row = (await screen.findByText('Session in progress')).closest('li') as HTMLElement;
  expect(row).toHaveTextContent('In progress since 10:02 GMT, 10 reps.');
  expect(row).not.toHaveTextContent(/rep \d+ of/);
  expect(scheduler.active).toBe(0);
});

describe('when the lumbar length was measured', () => {
  const withMeasuredAt = (lumbarMeasuredAt: string | null): ReturnType<typeof renderInWorld> =>
    setup({ data: new WithToday({ ...TODAY, lumbarMeasuredAt }) });

  it('says the day when the measure is not from today', async () => {
    withMeasuredAt('2026-11-03T08:00:00Z');
    expect(await screen.findByText('Lumbar length measured 3 Nov.')).toBeInTheDocument();
  });

  it('says nothing when it was measured today in GMT', async () => {
    withMeasuredAt('2026-11-05T01:00:00Z');
    await screen.findByRole('img', { name: /Side view of the spine/ });
    expect(screen.queryByText(/Lumbar length measured/)).not.toBeInTheDocument();
  });

  it('says nothing when there is no length at all', async () => {
    setup({ data: new WithToday({ ...TODAY, lumbarLengthMm: null, lumbarMeasuredAt: null }) });
    await screen.findByRole('img', { name: /Lumbar length No data/ });
    expect(screen.queryByText(/Lumbar length measured/)).not.toBeInTheDocument();
  });
});

describe('what to do today', () => {
  const todo = async (today: Partial<TodayData>): Promise<string | null | undefined> => {
    setup({ data: new WithToday({ ...TODAY, ...today }) });
    return (await screen.findByText('What to do today:')).parentElement?.textContent;
  };
  const plan = (n: number): string[] => ['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01', 'REC_INCREASE_ARED'].slice(0, n);

  it('amber counts one plan item in the singular', async () => {
    expect(await todo({ recommendations: plan(1) })).toBe('What to do today: the one plan item below, and your daily report.');
  });

  it('amber counts three plan items in digits', async () => {
    expect(await todo({ recommendations: plan(3) })).toBe('What to do today: the 3 plan items below, and your daily report.');
  });

  it('amber does not mention the daily report once it is done', async () => {
    expect(await todo({ recommendations: plan(2), alsoToday: ['TASK_ODI'] })).toBe('What to do today: the two plan items below.');
  });

  it('amber with one plan item and nothing else due ends after the item', async () => {
    expect(await todo({ recommendations: plan(1), alsoToday: [] })).toBe('What to do today: the one plan item below.');
  });

  it('amber with no plan and no report due says so without inventing a task', async () => {
    const text = await todo({ recommendations: [], alsoToday: [] });
    expect(text).toBe('What to do today: nothing is listed in the plan yet.');
  });

  it('green keeps the routine and adds the daily report only while it is due', async () => {
    expect(await todo({ level: 'green', recommendations: [], alsoToday: ['TASK_DAILY_REPORT'] })).toBe(
      'What to do today: your usual routine and your daily report.',
    );
  });

  it('green does not mention the daily report once it is done', async () => {
    expect(await todo({ level: 'green', recommendations: [], alsoToday: [] })).toBe('What to do today: your usual routine.');
  });
});

describe('live rep on Today (real data)', () => {
  const SID = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';
  const rep = (name: string): RepStateView => example<RepStateView>(name);
  const line = async (): Promise<HTMLElement> => (await screen.findByText('Session in progress')).closest('li') as HTMLElement;

  it('with signal it says "rep N of M"; without signal or lost it goes back to the line with no counter', async () => {
    const world = realSetup(example<TodayViewPayload>('ui-today.nodata'));
    await line();
    const row = (): HTMLElement => screen.getByText('Session in progress').closest('li') as HTMLElement;
    expect(row()).toHaveTextContent('In progress since 10:02 GMT, 10 reps.');
    expect(row()).not.toHaveTextContent('rep 3');
    act(() => {
      world.api.emitReps(SID, { kind: 'state', state: rep('ui-rep-state') });
    });
    expect(row()).toHaveTextContent('In progress since 10:02 GMT, rep 3 of 10.');
    act(() => {
      world.api.emitReps(SID, { kind: 'state', state: rep('ui-rep-state.no-signal') });
    });
    expect(row()).not.toHaveTextContent('rep 3');
    act(() => {
      world.api.emitReps(SID, { kind: 'state', state: rep('ui-rep-state') });
    });
    expect(row()).toHaveTextContent('rep 3 of 10');
    act(() => {
      world.api.emitReps(SID, { kind: 'lost' });
    });
    expect(row()).not.toHaveTextContent('rep 3');
  });

  it('unsubscribes on unmount and does not subscribe without a session in progress', async () => {
    const world = realSetup(example<TodayViewPayload>('ui-today.nodata'));
    await line();
    expect(world.api.repsListenerCount(SID)).toBe(1);
    world.unmount();
    expect(world.api.repsListenerCount(SID)).toBe(0);
    const other = realSetup(example<TodayViewPayload>('ui-today'));
    await screen.findAllByRole('heading', { level: 1, name: 'Amber' });
    expect(other.api.calls).not.toContain('reps.subscribe');
  });
});
