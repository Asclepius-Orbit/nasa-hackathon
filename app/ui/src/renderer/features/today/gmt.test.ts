import { expect, it } from 'vitest';

import { gmtClock, minutesSince } from './gmt';

it('writes an ISO time as GMT HH:MM', () => {
  expect(gmtClock('2026-11-05T03:05:59Z')).toBe('03:05');
  expect(gmtClock('2026-11-05T13:38:00.400Z')).toBe('13:38');
});

it('counts whole minutes since a time and never goes negative', () => {
  const now = new Date('2026-11-05T13:40:30Z');
  expect(minutesSince('2026-11-05T12:58:00Z', now)).toBe(42);
  expect(minutesSince('2026-11-05T13:41:00Z', now)).toBe(0);
});
