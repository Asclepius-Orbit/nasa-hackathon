import { describe, expect, it } from 'vitest';

import type { DayRecord } from '../../data/types';
import { buildLengthChart, buildPainChart, dayAxisTicks, dayName, deltaBetween, spotPosition } from './scales';

const day = (n: number, lengthMm: number | null, pain: number | null): DayRecord => ({
  date: `2026-11-${String(n).padStart(2, '0')}`,
  day: n,
  level: lengthMm === null ? 'nodata' : 'green',
  lengthMm,
  pain,
  session: null,
  report: null,
});

const days = [day(1, 0, 2), day(2, 1, 4), day(3, null, null), day(4, null, null), day(5, -1, 0)];
const range = { lo: -1, hi: 1 };

describe('buildLengthChart', () => {
  it('breaks the line at days without data instead of joining or zeroing them', () => {
    const chart = buildLengthChart(days, range);
    expect(chart.segments).toHaveLength(2);
    expect(chart.segments[0]?.points).toHaveLength(2);
    expect(chart.segments[1]?.points).toHaveLength(1);
  });

  it('maps higher values higher on the chart and the range to a band', () => {
    const chart = buildLengthChart(days, range);
    const [a, b] = chart.segments[0]?.points ?? [];
    expect(b?.y).toBeLessThan(a?.y ?? 0);
    expect(chart.band?.height).toBeGreaterThan(0);
    expect(chart.band?.y).toBeLessThan(a?.y ?? 0);
    expect(a?.x).toBeLessThan(b?.x ?? 0);
  });

  it('reports each run of missing days as a hatched gap with its day labels', () => {
    const chart = buildLengthChart(days, range);
    expect(chart.gaps).toHaveLength(1);
    expect(chart.gaps[0]?.label).toBe('3 to 4');
    expect(chart.gaps[0]?.width).toBeGreaterThan(0);
  });

  it('labels a single missing day with its number and offers y ticks at the range edges', () => {
    const chart = buildLengthChart([day(1, 0, 0), day(2, null, null), day(3, 0, 0)], range);
    expect(chart.gaps[0]?.label).toBe('2');
    expect(chart.yTicks.map((t) => t.mm)).toEqual([3, 1, -1]);
  });

  it('clamps values far outside the plot', () => {
    const chart = buildLengthChart([day(1, 99, 0)], range);
    expect(chart.segments[0]?.points[0]?.y).toBeGreaterThanOrEqual(10);
  });

  it('handles no days', () => {
    expect(buildLengthChart([], range).segments).toEqual([]);
  });
});

describe('buildPainChart', () => {
  it('draws a bar only for reported days, taller for more pain, and no bar for null', () => {
    const chart = buildPainChart(days);
    expect(chart.bars.map((b) => b.date)).toEqual(['2026-11-01', '2026-11-02', '2026-11-05']);
    const [one, two] = chart.bars;
    expect(two?.height).toBeGreaterThan(one?.height ?? 0);
    expect(chart.gaps).toHaveLength(1);
  });

  it('keeps a reported zero as an empty bar position, not a gap', () => {
    const chart = buildPainChart(days);
    expect(chart.bars.find((b) => b.date === '2026-11-05')?.height).toBe(0);
    expect(chart.gaps[0]?.label).toBe('3 to 4');
  });
});

describe('dayAxisTicks', () => {
  const many = Array.from({ length: 30 }, (_, i) => day(58 + i, 0, 0));
  it('labels the first day, every seventh, and today', () => {
    const ticks = dayAxisTicks(many, 87);
    expect(ticks.map((t) => t.label)).toEqual(['Day 58', 'Day 65', 'Day 72', 'Day 79', 'Today, 87']);
    expect(ticks[0]?.percent).toBeCloseTo((0.5 / 30) * 100);
  });
  it('is empty without days', () => {
    expect(dayAxisTicks([], 1)).toEqual([]);
  });
});

describe('spotPosition', () => {
  it('returns percentages, clamped to the scale', () => {
    expect(spotPosition(0, -2, 3, { lo: -1, hi: 1 })).toEqual({ dot: 40, bandLeft: 20, bandWidth: 40 });
    expect(spotPosition(99, -2, 3, null).dot).toBe(100);
    expect(spotPosition(-99, -2, 3, null).dot).toBe(0);
    expect(spotPosition(5, 0, 10, null).bandWidth).toBeNull();
  });
});

describe('deltaBetween', () => {
  it('says up, down or same by the displayed precision', () => {
    expect(deltaBetween(2, 1, 1)).toEqual({ direction: 'up', amount: 1 });
    expect(deltaBetween(1, 2, 1)).toEqual({ direction: 'down', amount: 1 });
    expect(deltaBetween(1.01, 1, 1)).toEqual({ direction: 'same', amount: 0 });
  });
  it('has nothing to compare against a missing value', () => {
    expect(deltaBetween(1, null, 1)).toBeNull();
  });
});

describe('days without a launch date and ranges without a reference', () => {
  const undated = { ...day(1, null, null), day: null };

  it('names a day by its mission day, or by its calendar date when there is no mission day', () => {
    expect(dayName(day(7, 0, 0))).toBe('7');
    expect(dayName(undated)).toBe('2026-11-01');
  });

  it('labels gaps and ticks with the date when the mission day is not known', () => {
    const chart = buildLengthChart([undated, { ...day(2, 0, 0), day: null }], range);
    expect(chart.gaps[0]?.label).toBe('2026-11-01');
    expect(chart.xTicks[0]?.label).toBe('2026-11-01');
    expect(dayAxisTicks([undated], null)[0]?.label).toBe('2026-11-01');
  });

  it('draws no reference band and no range ticks without a preflight range, and never a zero line of data', () => {
    const chart = buildLengthChart(days, null);
    expect(chart.band).toBeNull();
    expect(chart.yTicks.map((t) => t.mm)).toEqual([3, 0]);
  });

  it('never marks a day as today when today is not known', () => {
    expect(dayAxisTicks([day(1, 0, 0), day(2, 0, 0)], null).some((t) => t.isToday)).toBe(false);
  });
});
