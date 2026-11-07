import type { Settings } from '../../shared/api';

const MINUS = '−';
const MM_PER_INCH = 25.4;
const MS_PER_MINUTE = 60_000;
const MINUTES_PER_DAY = 1440;

const two = (n: number): string => String(n).padStart(2, '0');

/** Signed length change: `+2.1 mm`, or inches with two decimals (`+0.08 in`). A value that rounds to zero has no sign. */
export function formatLength(mm: number, unit: Settings['lengthUnit']): string {
  const value = unit === 'mm' ? mm : mm / MM_PER_INCH;
  const digits = unit === 'mm' ? 1 : 2;
  const text = Math.abs(value).toFixed(digits);
  const isZero = Number(text) === 0;
  const sign = isZero ? '' : value > 0 ? '+' : MINUS;
  return `${sign}${text} ${unit}`;
}

/** Clock time in GMT (`13:40`); `gmt_met` appends mission elapsed time since `missionStart` (`, MET 86 d 13:40`). */
export function formatTime(date: Date, timeFormat: Settings['timeFormat'], missionStart: Date): string {
  const gmt = `${two(date.getUTCHours())}:${two(date.getUTCMinutes())}`;
  if (timeFormat === 'gmt') return gmt;
  const elapsed = Math.floor((date.getTime() - missionStart.getTime()) / MS_PER_MINUTE);
  const sign = elapsed < 0 ? MINUS : '';
  const total = Math.abs(elapsed);
  const days = Math.floor(total / MINUTES_PER_DAY);
  const rest = total % MINUTES_PER_DAY;
  return `${gmt}, MET ${sign}${String(days)} d ${two(Math.floor(rest / 60))}:${two(rest % 60)}`;
}
