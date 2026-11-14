import { describe, expect, it } from 'vitest';

import { formatLength, formatTime } from './format';

describe('formatLength', () => {
  it('shows mm with sign and one decimal', () => {
    expect(formatLength(2.1, 'mm')).toBe('+2.1 mm');
    expect(formatLength(-0.44, 'mm')).toBe('−0.4 mm');
  });
  it('shows a plain zero when the value rounds to nothing', () => {
    expect(formatLength(0.04, 'mm')).toBe('0.0 mm');
    expect(formatLength(-0.04, 'in')).toBe('0.00 in');
  });
  it('converts to inches with two decimals', () => {
    expect(formatLength(25.4, 'in')).toBe('+1.00 in');
    expect(formatLength(-2.54, 'in')).toBe('−0.10 in');
  });
});

describe('formatTime', () => {
  const now = new Date('2026-11-05T13:40:30Z');
  const start = new Date('2026-08-11T00:00:00Z');
  it('shows GMT as HH:MM in UTC', () => {
    expect(formatTime(now, 'gmt', start)).toBe('13:40');
    expect(formatTime(new Date('2026-11-05T03:05:00Z'), 'gmt', start)).toBe('03:05');
  });
  it('appends mission elapsed time as days and HH:MM', () => {
    expect(formatTime(now, 'gmt_met', start)).toBe('13:40, MET 86 d 13:40');
  });
  it('does not invent a mission elapsed time without a launch date', () => {
    expect(formatTime(now, 'gmt_met', null)).toBe('13:40, MET not known');
    expect(formatTime(now, 'gmt', null)).toBe('13:40');
  });
  it('marks a time before the mission start', () => {
    expect(formatTime(new Date('2026-08-10T22:30:00Z'), 'gmt_met', start)).toBe('22:30, MET −0 d 01:30');
  });
});
