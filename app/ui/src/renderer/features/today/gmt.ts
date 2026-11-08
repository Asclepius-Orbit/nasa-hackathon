/** `13:38` in GMT from an ISO timestamp. */
export function gmtClock(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;

/** `3 Nov`: the GMT day of an ISO timestamp. */
export function gmtDay(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getUTCDate())} ${MONTHS[d.getUTCMonth()] ?? ''}`;
}

/** Whether an ISO timestamp falls on the same GMT calendar day as `now`. */
export function isSameGmtDay(iso: string, now: Date): boolean {
  const d = new Date(iso);
  return d.getUTCFullYear() === now.getUTCFullYear() && d.getUTCMonth() === now.getUTCMonth() && d.getUTCDate() === now.getUTCDate();
}

/** `09:58` for a time from today (GMT), `3 Nov 09:58` for any other day. */
export function gmtStamp(iso: string, now: Date): string {
  return isSameGmtDay(iso, now) ? gmtClock(iso) : `${gmtDay(iso)} ${gmtClock(iso)}`;
}

/** Signed whole minutes from `fromIso` to `toIso` (negative when `toIso` is earlier), truncated toward zero. */
export function minutesBetween(fromIso: string, toIso: string): number {
  // `|| 0` keeps a sub-minute negative difference from becoming -0.
  return Math.trunc((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 60_000) || 0;
}

/** Whole minutes between an ISO timestamp and now, never negative. */
export function minutesSince(iso: string, now: Date): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
}
