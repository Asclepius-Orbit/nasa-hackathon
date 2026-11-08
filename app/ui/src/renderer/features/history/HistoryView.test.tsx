import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import type { DayRecord, HistoryData } from '../../data/types';
import { RealDataSource } from '../../data/real/RealDataSource';
import type { HistoryView as HistoryPayload } from '../../../shared/api';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { FakeScheduler } from '../../test/fake-scheduler';
import { renderInWorld } from '../../test/providers';
import { HistoryView } from './HistoryView';

const strip = (): HTMLElement => screen.getByRole('group', { name: /mission days 58 to 87/i });
const dayButton = (n: number): HTMLElement => within(strip()).getByRole('button', { name: new RegExp(`^day ${String(n)},`, 'i') });
const detail = (): HTMLElement => screen.getByTestId('day-detail');

async function renderHistory(api = new FakeAsclepiusApi()): Promise<void> {
  renderInWorld(<HistoryView />, { api });
  await screen.findByTestId('day-detail');
}

it('titles the screen and describes both charts to assistive technology', async () => {
  await renderHistory();
  expect(screen.getByRole('heading', { level: 1, name: 'Last 30 days' })).toBeInTheDocument();
  const length = screen.getByRole('img', { name: /lumbar length.*preflight range.*no data on days 80 to 82/i });
  const pain = screen.getByRole('img', { name: /pain.*no reports on days 80 to 82/i });
  expect(length).toBeInTheDocument();
  expect(pain).toBeInTheDocument();
  expect(length.getAttribute('aria-label')).toMatch(/day 87/);
});

it('draws the line in two pieces around the no-data days and a bar per reported day', async () => {
  await renderHistory();
  expect(screen.getAllByTestId('length-segment')).toHaveLength(2);
  expect(screen.getAllByTestId('pain-bar')).toHaveLength(27);
  expect(screen.getAllByTestId('nodata-gap')).toHaveLength(2);
  expect(screen.getAllByText('80 to 82')).toHaveLength(2);
});

it('labels the length axis in the chosen unit', async () => {
  const api = new FakeAsclepiusApi();
  api.settingsValue = { ...api.settingsValue, lengthUnit: 'in' };
  await renderHistory(api);
  expect(await screen.findAllByText('+0.12 in')).not.toHaveLength(0);
  expect(within(detail()).getByText('+0.08 in')).toBeInTheDocument();
});

it('opens on today with the matching detail', async () => {
  await renderHistory();
  expect(dayButton(87)).toHaveAttribute('aria-pressed', 'true');
  expect(dayButton(86)).toHaveAttribute('aria-pressed', 'false');
  expect(within(detail()).getByText('Day 87')).toBeInTheDocument();
  expect(within(detail()).getByText('today')).toBeInTheDocument();
  expect(within(detail()).getByText('+2.1 mm')).toBeInTheDocument();
  expect(within(detail()).getByText('4 of 10')).toBeInTheDocument();
  expect(within(detail()).getByText('Up 0.2 mm from day 86')).toBeInTheDocument();
  expect(within(detail()).getByText('Same as day 86')).toBeInTheDocument();
});

it('shows explicit no data for a day without measurements, never a zero or a green', async () => {
  await renderHistory();
  await userEvent.click(dayButton(81));
  expect(within(detail()).getAllByText('No data').length).toBeGreaterThanOrEqual(3);
  expect(within(detail()).queryByText(/of 10/)).not.toBeInTheDocument();
  expect(within(detail()).queryByText('Green')).not.toBeInTheDocument();
  expect(within(detail()).getByText(/No data is not the same as fine/)).toBeInTheDocument();
});

it('compares with the previous day that has data', async () => {
  await renderHistory();
  await userEvent.click(dayButton(83));
  expect(within(detail()).getByText("Up 0.9 mm from day 79")).toBeInTheDocument();
  expect(within(detail()).getByText("Up 1 from day 79")).toBeInTheDocument();
});

it('summarises and counts the days', async () => {
  await renderHistory();
  expect(screen.getByText('Amber for the last 5 days. 3 days without data. Green for 22 days before that.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^All 30$/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^Amber 5$/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^Green 22$/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^No data 3$/ })).toBeInTheDocument();
});

it('filters days without hiding them from the strip', async () => {
  await renderHistory();
  const nodata = screen.getByRole('button', { name: /^No data 3$/ });
  await userEvent.click(nodata);
  expect(nodata).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: /^All 30$/ })).toHaveAttribute('aria-pressed', 'false');
  expect(dayButton(81)).not.toHaveAttribute('data-dim');
  expect(dayButton(60)).toHaveAttribute('data-dim', 'true');
  await userEvent.click(screen.getByRole('button', { name: /^All 30$/ }));
  expect(dayButton(60)).not.toHaveAttribute('data-dim');
});

it('moves between days with the previous and next buttons and disables them at the ends', async () => {
  await renderHistory();
  const next = screen.getByRole('button', { name: 'Next day' });
  expect(next).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Previous day' }));
  expect(dayButton(86)).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(next);
  expect(dayButton(87)).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(dayButton(58));
  expect(screen.getByRole('button', { name: 'Previous day' })).toBeDisabled();
});

it('moves with the arrow keys and keeps focus on the selected day (roving tabindex)', async () => {
  await renderHistory();
  expect(dayButton(87)).toHaveAttribute('tabindex', '0');
  expect(dayButton(86)).toHaveAttribute('tabindex', '-1');
  dayButton(87).focus();
  await userEvent.keyboard('{ArrowLeft}');
  expect(dayButton(86)).toHaveAttribute('aria-pressed', 'true');
  expect(dayButton(86)).toHaveFocus();
  await userEvent.keyboard('{ArrowRight}{ArrowRight}');
  expect(dayButton(87)).toHaveFocus();
  await userEvent.keyboard('{Home}');
  expect(dayButton(58)).toHaveFocus();
  await userEvent.keyboard('{End}');
  expect(dayButton(87)).toHaveFocus();
  await userEvent.keyboard('{ArrowUp}');
  expect(dayButton(87)).toHaveFocus();
});

it('lists the changes of level, newest first, and "Show day" selects that day', async () => {
  await renderHistory();
  const list = screen.getByRole('list', { name: 'Changes of level' });
  const items = within(list).getAllByRole('listitem');
  expect(items).toHaveLength(3);
  expect(items[0]).toHaveTextContent('Day 83');
  expect(items[1]).toHaveTextContent('Days 80 to 82');
  expect(items[2]).toHaveTextContent('From day 58');
  await userEvent.click(within(items[1] as HTMLElement).getByRole('button', { name: 'Show day 80' }));
  expect(dayButton(80)).toHaveAttribute('aria-pressed', 'true');
  expect(within(detail()).getByText('Day 80')).toBeInTheDocument();
});

it('labels the strip axis with today', async () => {
  await renderHistory();
  expect(screen.getByText('Today, 87')).toBeInTheDocument();
  expect(screen.getByText('Day 65')).toBeInTheDocument();
});

it('shows a loading state until the data arrives', () => {
  renderInWorld(<HistoryView />);
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
});

class HistorySource extends MockDataSource {
  constructor(private readonly days: readonly DayRecord[], private readonly todayDay: number | null, private readonly range: HistoryData['preflightRangeMm'] = { lo: -1, hi: 1 }) {
    super({ now: () => new Date('2026-11-05T13:40:00Z'), scheduler: new FakeScheduler() });
  }
  override getHistory(): Promise<HistoryData> {
    return Promise.resolve({ from: '', to: '', days: this.days, todayDay: this.todayDay, preflightRangeMm: this.range });
  }
}

const rec = (day: number | null, lengthMm: number | null, pain: number | null, date = `2026-11-${String(day ?? 0).padStart(2, '0')}`): DayRecord => ({
  date,
  day,
  level: lengthMm === null ? 'nodata' : 'green',
  lengthMm,
  pain,
  session: null,
  report: { kind: 'daily', redFlags: [] },
});

it('describes a month with no gaps, a value below the range and one lone missing day', async () => {
  const days = [rec(1, -2, 3), rec(2, null, null), rec(3, -2, 0)];
  renderInWorld(<HistoryView />, { data: new HistorySource(days, 3) });
  await screen.findByTestId('day-detail');
  expect(screen.getByRole('img', { name: /below the range\. No data on day 2\./ })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: /Latest 0 of 10 on day 3\. No reports on day 2\./ })).toBeInTheDocument();
});

it('says so when every day has data, and never invents a gap', async () => {
  renderInWorld(<HistoryView />, { data: new HistorySource([rec(1, 0, 1), rec(2, 0, 1)], 2) });
  await screen.findByTestId('day-detail');
  expect(screen.getByRole('img', { name: /No days without data\./ })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: /Reported every day\./ })).toBeInTheDocument();
  expect(screen.queryAllByTestId('nodata-gap')).toHaveLength(0);
  expect(within(detail()).getAllByText('Same as day 1')).toHaveLength(2);
});

it('copes with an empty month', async () => {
  renderInWorld(<HistoryView />, { data: new HistorySource([], 1) });
  expect(await screen.findByRole('img', { name: /Lumbar length/ })).toBeInTheDocument();
  expect(screen.queryByTestId('day-detail')).not.toBeInTheDocument();
});

it('shows an alert when the history cannot be loaded', async () => {
  class Broken extends MockDataSource {
    override getHistory(): Promise<HistoryData> {
      return Promise.reject(new Error('boom'));
    }
  }
  renderInWorld(<HistoryView />, { data: new Broken({ now: () => new Date(), scheduler: new FakeScheduler() }) });
  expect(await screen.findByRole('alert')).toHaveTextContent('The history could not be loaded.');
});

const realHistory = (view: HistoryPayload | null): void => {
  const api = new FakeAsclepiusApi();
  if (view) api.viewData.history = view;
  renderInWorld(<HistoryView />, { api, data: new RealDataSource(api.views) });
};

it('shows the backend example: nodata day empty, the report written from codes, red flags named', async () => {
  const view = example<HistoryPayload>('ui-history');
  const last = view.days[2];
  if (!last) throw new Error('example changed');
  realHistory({ ...view, days: [view.days[0], view.days[1], { ...last, report: { kind: 'red_flag', red_flags: ['leg_weakness'] } }] as HistoryPayload['days'] });
  await screen.findByTestId('day-detail');
  expect(within(detail()).getByText('Day 87')).toBeInTheDocument();
  expect(within(detail()).getByText('Warning symptom reported: Leg weakness')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /^Day 85,/ }));
  expect(within(detail()).getAllByText('No data').length).toBeGreaterThanOrEqual(3);
  expect(within(detail()).getByText('Not done')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /^Day 86,/ }));
  expect(within(detail()).getByText(/standing lat pulldown, complete/)).toBeInTheDocument();
  expect(within(detail()).getByText('Daily report done')).toBeInTheDocument();
});

it('with no launch date every day is named by its date and none is marked as today', async () => {
  const view = example<HistoryPayload>('ui-history');
  realHistory({ ...view, today_day: null, days: view.days.map((d) => ({ ...d, mission_day: null })) });
  await screen.findByTestId('day-detail');
  expect(within(detail()).getByText('2027-02-09')).toBeInTheDocument();
  expect(within(detail()).queryByText('today')).not.toBeInTheDocument();
  expect(screen.getByRole('group', { name: /Mission days 2027-02-07 to 2027-02-09/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Show day 2027-02-07' })).toBeInTheDocument();
});

it('says there is no preflight range yet instead of drawing one', async () => {
  const view = example<HistoryPayload>('ui-history');
  realHistory({ ...view, preflight_range_mm: null });
  await screen.findByTestId('day-detail');
  expect(screen.getByText(/No preflight reference yet/)).toBeInTheDocument();
  expect(screen.getByRole('img', { name: /with no preflight range yet/ })).toBeInTheDocument();
});

it('shows an empty window as empty, not as a green month', async () => {
  realHistory(example<HistoryPayload>('ui-history.empty'));
  expect(await screen.findByRole('img', { name: /Lumbar length/ })).toBeInTheDocument();
  expect(screen.queryByTestId('day-detail')).not.toBeInTheDocument();
  expect(screen.queryAllByTestId('length-segment')).toHaveLength(0);
});

it('says there is no connection to the monitor when the backend is down', async () => {
  realHistory(null);
  expect(await screen.findByTestId('no-monitor')).toBeInTheDocument();
  expect(screen.queryByTestId('day-detail')).not.toBeInTheDocument();
});
