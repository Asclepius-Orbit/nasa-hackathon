/** Copy of the History screen. */
import type { DayLevel, DayRecord } from '../data/types';
import { protocolShort, redFlagLabels, reportKindText, sessionStateText } from './codes';

type DayId = Pick<DayRecord, 'day' | 'date'>;

export const history = {
  title: 'Last 30 days',
  lede: 'Each session against your preflight reference. Days without data are left empty, never filled in.',
  loading: 'Loading history',
  loadFailed: 'The history could not be loaded.',
  lengthTitle: 'Lumbar length change',
  lengthSub: 'Stretch cord C3 to S1, in mm, against your preflight reference in 1 g.',
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
  noRange: 'No preflight reference yet, so there is no range to compare with.',
  emptyWindow: 'There are no days in this window.',
  none: 'none',
  notDone: 'Not done',
} as const;

/** A day by its mission day, or by its calendar date when there is no launch date. */
export const dayText = (d: DayId): string => (d.day === null ? d.date : `Day ${String(d.day)}`);
const dayLower = (d: DayId): string => (d.day === null ? d.date : `day ${String(d.day)}`);
const dayNumber = (d: DayId): string => (d.day === null ? d.date : String(d.day));

/** Ends of the length gauge in the day detail, in mm. */
export const lengthGauge = { lo: -2, hi: 3 } as const;

export const stripLabel = (first: DayId, last: DayId): string =>
  `Mission days ${dayNumber(first)} to ${dayNumber(last)}. Use the arrow keys to move between days.`;

export const dayButtonLabel = (d: DayId, level: string): string => `${dayText(d)}, ${level}`;
export const dayHeading = dayText;
export const painOfTen = (value: number): string => `${String(value)} of 10`;
export const showDayLabel = (d: DayId): string => `${history.showDay} ${dayNumber(d)}`;

export const daysSummary = (amber: number, nodata: number, green: number): string =>
  `Amber for the last ${String(amber)} days. ${String(nodata)} days without data. Green for ${String(green)} days before that.`;

export function deltaText(direction: 'up' | 'down' | 'same', amount: string, from: DayId): string {
  const fromText = dayLower(from);
  return direction === 'same' ? `Same as ${fromText}` : `${direction === 'up' ? 'Up' : 'Down'} ${amount} from ${fromText}`;
}

export function changeWhen(first: DayId, last: DayId, isFirstRun: boolean, level: DayLevel): string {
  if (level === 'nodata' && last.date !== first.date) return `Days ${dayNumber(first)} to ${dayNumber(last)}`;
  return isFirstRun ? `From ${dayLower(first)}` : dayText(first);
}

/** What a day says about itself, written from its level alone: the backend sends codes, never a sentence. */
export const dayNote = (level: DayLevel): string =>
  ({
    nodata: 'No session and no report: not assessed. No data is not the same as fine.',
    green: 'Everything inside your reference.',
    amber: 'A change from your reference.',
    red: 'Outside your reference: the crew medical officer was alerted.',
  })[level];

export function sessionLine(session: DayRecord['session']): string {
  return session === null ? 'None' : `${protocolShort(session.protocolId)}, ${sessionStateText(session.state)}`;
}

export function reportLine(report: DayRecord['report']): string {
  if (report === null) return history.notDone;
  const flags = report.redFlags.map((code) => redFlagLabels[code]);
  return flags.length === 0 ? reportKindText(report.kind) : `${reportKindText(report.kind)}: ${flags.join('; ')}`;
}

const gapDays = (labels: readonly string[]): string =>
  `${labels.some((l) => l.includes(' to ')) || labels.length > 1 ? 'days' : 'day'} ${labels.join(' and ')}`;

export function lengthChartLabel(args: {
  first: string;
  last: string;
  latest: { day: string; text: string; position: 'above' | 'inside' | 'below' | null } | null;
  /** `null` when there is no preflight reference. */
  rangeText: string | null;
  gapLabels: readonly string[];
}): string {
  const range = args.rangeText === null ? 'with no preflight range yet' : `against your preflight range of ${args.rangeText}`;
  const parts = [`Lumbar length, days ${args.first} to ${args.last}, ${range}.`];
  if (args.latest) {
    parts.push(`Latest value ${args.latest.text} on day ${args.latest.day}${args.latest.position === null ? '' : `, ${args.latest.position} the range`}.`);
  }
  parts.push(args.gapLabels.length > 0 ? `No data on ${gapDays(args.gapLabels)}.` : 'No days without data.');
  return parts.join(' ');
}

export function painChartLabel(args: {
  first: string;
  last: string;
  latest: { day: string; value: number } | null;
  gapLabels: readonly string[];
}): string {
  const parts = [`Pain, daily report 0 to 10, days ${args.first} to ${args.last}.`];
  if (args.latest) parts.push(`Latest ${String(args.latest.value)} of 10 on day ${args.latest.day}.`);
  parts.push(args.gapLabels.length > 0 ? `No reports on ${gapDays(args.gapLabels)}.` : 'Reported every day.');
  return parts.join(' ');
}
