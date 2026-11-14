import { expect, it } from 'vitest';

import { gmtClock, gmtDay, gmtStamp, isSameGmtDay, minutesBetween, minutesSince } from './gmt';

it('writes an ISO time as GMT HH:MM', () => {
  expect(gmtClock('2026-11-05T03:05:59Z')).toBe('03:05');
  expect(gmtClock('2026-11-05T13:38:00.400Z')).toBe('13:38');
});

it('counts whole minutes since a time and never goes negative', () => {
  const now = new Date('2026-11-05T13:40:30Z');
  expect(minutesSince('2026-11-05T12:58:00Z', now)).toBe(42);
  expect(minutesSince('2026-11-05T13:41:00Z', now)).toBe(0);
});

it('writes the GMT day as day and short month', () => {
  expect(gmtDay('2026-11-03T08:00:00Z')).toBe('3 Nov');
  expect(gmtDay('2027-02-09T23:59:59Z')).toBe('9 Feb');
  expect(gmtDay('2027-02-09T23:59:59-05:00')).toBe('10 Feb');
});

it('compares days in GMT, not in the local time zone', () => {
  const now = new Date('2026-11-05T00:10:00Z');
  expect(isSameGmtDay('2026-11-05T23:50:00Z', now)).toBe(true);
  expect(isSameGmtDay('2026-11-04T23:50:00Z', now)).toBe(false);
  expect(isSameGmtDay('2026-12-05T00:10:00Z', now)).toBe(false);
  expect(isSameGmtDay('2025-11-05T00:10:00Z', now)).toBe(false);
});

it('stamps a time with its day only when it is not today', () => {
  const now = new Date('2026-11-05T13:40:00Z');
  expect(gmtStamp('2026-11-05T09:58:00Z', now)).toBe('09:58');
  expect(gmtStamp('2026-11-03T09:58:00Z', now)).toBe('3 Nov 09:58');
});

it('gives the signed whole minutes from one time to another', () => {
  expect(minutesBetween('2026-11-05T13:30:00Z', '2026-11-05T13:38:50Z')).toBe(8);
  expect(minutesBetween('2026-11-05T13:38:00Z', '2026-11-05T13:30:00Z')).toBe(-8);
  expect(minutesBetween('2026-11-05T13:30:00Z', '2026-11-05T13:30:00Z')).toBe(0);
  expect(minutesBetween('2026-11-05T13:38:00Z', '2026-11-05T13:37:30Z')).toBe(0);
});
