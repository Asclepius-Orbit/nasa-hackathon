/** Copy of the History screen. */
import type { DayLevel } from '../data/types';

export const history = {
  title: 'Last 30 days',
  lede: 'Each session against your preflight reference. Days without data are left empty, never filled in.',
  loading: 'Loading history',
  loadFailed: 'The history could not be loaded.',
  lengthTitle: 'Lumbar length change',
  lengthSub: 'Stretch cord L1 to L5, in mm, against your preflight reference in 1 g.',
  legendRange: 'Your preflight range',
  legendValue: 'Session value',
  legendNoData: 'No data',
  painTitle: 'Pain',
  painSub: 'Daily report, 0 to 10.',
  daysTitle: 'Assessment by day',
  filterGroup: 'Show days',
  filterAll: 'All',
  filterAmber: 'Amber',
  filterGreen: 'Green',
  filterNoData: 'No data',
  previousDay: 'Previous day',
  nextDay: 'Next day',
  today: 'today',
  lumbarLength: 'Lumbar length',
  pain: 'Pain',
  dailyReport: 'Daily report',
  session: 'Session:',
  changesTitle: 'Changes of level',
  showDay: 'Show day',
  noData: 'No data',
} as const;

/** Ends of the length gauge in the day detail, in mm. */
export const lengthGauge = { lo: -2, hi: 3 } as const;

export const stripLabel = (first: number, last: number): string =>
  `Mission days ${String(first)} to ${String(last)}. Use the arrow keys to move between days.`;

export const dayButtonLabel = (day: number, level: string): string => `Day ${String(day)}, ${level}`;
export const dayHeading = (day: number): string => `Day ${String(day)}`;
export const painOfTen = (value: number): string => `${String(value)} of 10`;
export const showDayLabel = (day: number): string => `${history.showDay} ${String(day)}`;

export const daysSummary = (amber: number, nodata: number, green: number): string =>
  `Amber for the last ${String(amber)} days. ${String(nodata)} days without data. Green for ${String(green)} days before that.`;

export function deltaText(direction: 'up' | 'down' | 'same', amount: string, fromDay: number): string {
  const from = `day ${String(fromDay)}`;
  return direction === 'same' ? `Same as ${from}` : `${direction === 'up' ? 'Up' : 'Down'} ${amount} from ${from}`;
}

export function changeWhen(first: number, last: number, isFirstRun: boolean, level: DayLevel): string {
  if (level === 'nodata' && last > first) return `Days ${String(first)} to ${String(last)}`;
  return isFirstRun ? `From day ${String(first)}` : `Day ${String(first)}`;
}

const gapDays = (labels: readonly string[]): string =>
  `${labels.some((l) => l.includes(' to ')) || labels.length > 1 ? 'days' : 'day'} ${labels.join(' and ')}`;

export function lengthChartLabel(args: {
  first: number;
  last: number;
  latest: { day: number; text: string; position: 'above' | 'inside' | 'below' } | null;
  rangeText: string;
  gapLabels: readonly string[];
}): string {
  const parts = [`Lumbar length, days ${String(args.first)} to ${String(args.last)}, against your preflight range of ${args.rangeText}.`];
  if (args.latest) parts.push(`Latest value ${args.latest.text} on day ${String(args.latest.day)}, ${args.latest.position} the range.`);
  parts.push(args.gapLabels.length > 0 ? `No data on ${gapDays(args.gapLabels)}.` : 'No days without data.');
  return parts.join(' ');
}

export function painChartLabel(args: {
  first: number;
  last: number;
  latest: { day: number; value: number } | null;
  gapLabels: readonly string[];
}): string {
  const parts = [`Pain, daily report 0 to 10, days ${String(args.first)} to ${String(args.last)}.`];
  if (args.latest) parts.push(`Latest ${String(args.latest.value)} of 10 on day ${String(args.latest.day)}.`);
  parts.push(args.gapLabels.length > 0 ? `No reports on ${gapDays(args.gapLabels)}.` : 'Reported every day.');
  return parts.join(' ');
}
