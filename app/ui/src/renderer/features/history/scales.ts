import type { DayRecord } from '../../data/types';

/** Chart geometry in viewBox units (the mock draws on a 960-wide canvas with a 60 px label gutter). */
const PLOT_LEFT = 60;
const PLOT_WIDTH = 880;
const PLOT_TOP = 10;
const LENGTH_BOTTOM = 190;
const MM_TO_Y = 25;
const LENGTH_ZERO_Y = 115;
const PAIN_BASELINE = 110;
const PAIN_UNIT = 10;
const PAIN_TOP = 10;
const BAR_WIDTH = 20;
const TICK_CLEARANCE = 110;
const LENGTH_TOP_TICK_MM = 3;

export interface Point {
  x: number;
  y: number;
  day: number;
}
export interface Gap {
  x: number;
  width: number;
  /** "80 to 82", or "80" for one day. */
  label: string;
  labelX: number;
}
export interface XTick {
  label: string;
  x: number;
  anchor: 'start' | 'middle' | 'end';
}
export interface LengthChart {
  segments: { points: Point[] }[];
  gaps: Gap[];
  band: { y: number; height: number };
  yTicks: { mm: number; y: number }[];
  xTicks: XTick[];
  baselineY: number;
}
export interface PainChart {
  bars: { day: number; x: number; y: number; height: number; width: number }[];
  gaps: Gap[];
  xTicks: XTick[];
  baselineY: number;
  yTicks: { value: number; y: number }[];
}

const slotWidth = (count: number): number => PLOT_WIDTH / Math.max(count, 1);
const centreX = (index: number, count: number): number => PLOT_LEFT + (index + 0.5) * slotWidth(count);
const clamp = (value: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, value));
const lengthY = (mm: number): number => clamp(LENGTH_ZERO_Y - MM_TO_Y * mm, PLOT_TOP, LENGTH_BOTTOM);

/** Runs of consecutive days where `missing` holds, as [firstIndex, lastIndex]. */
function runs(days: readonly DayRecord[], missing: (d: DayRecord) => boolean): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  days.forEach((d, i) => {
    if (missing(d)) {
      if (start < 0) start = i;
    } else if (start >= 0) {
      out.push([start, i - 1]);
      start = -1;
    }
  });
  if (start >= 0) out.push([start, days.length - 1]);
  return out;
}

function gapsOf(days: readonly DayRecord[], missing: (d: DayRecord) => boolean): Gap[] {
  const slot = slotWidth(days.length);
  return runs(days, missing).map(([from, to]) => {
    const first = days[from]?.day ?? 0;
    const last = days[to]?.day ?? 0;
    const x = PLOT_LEFT + from * slot;
    const width = (to - from + 1) * slot;
    return { x, width, label: first === last ? String(first) : `${String(first)} to ${String(last)}`, labelX: x + width / 2 };
  });
}

function xTicksOf(days: readonly DayRecord[], gaps: readonly Gap[]): XTick[] {
  if (days.length === 0) return [];
  const last = days.length - 1;
  const picks = [...new Set([0, Math.floor(last / 2), last])];
  return picks
    .map((i): XTick => ({
      label: `Day ${String(days[i]?.day ?? 0)}`,
      x: i === 0 ? PLOT_LEFT : i === last ? PLOT_LEFT + PLOT_WIDTH : centreX(i, days.length),
      anchor: i === 0 ? 'start' : i === last ? 'end' : 'middle',
    }))
    .filter((t) => t.anchor === 'middle' ? gaps.every((g) => Math.abs(g.labelX - t.x) >= TICK_CLEARANCE) : true);
}

/** Lumbar length: one line segment per run of measured days; days with `null` are gaps, never joined or zeroed. */
export function buildLengthChart(days: readonly DayRecord[], range: { lo: number; hi: number }): LengthChart {
  const segments: { points: Point[] }[] = [];
  let current: Point[] | null = null;
  days.forEach((d, i) => {
    if (d.lengthMm === null) {
      current = null;
      return;
    }
    if (current === null) {
      current = [];
      segments.push({ points: current });
    }
    current.push({ x: centreX(i, days.length), y: lengthY(d.lengthMm), day: d.day });
  });
  const gaps = gapsOf(days, (d) => d.lengthMm === null);
  return {
    segments,
    gaps,
    band: { y: lengthY(range.hi), height: lengthY(range.lo) - lengthY(range.hi) },
    yTicks: [LENGTH_TOP_TICK_MM, range.hi, range.lo].map((mm) => ({ mm, y: lengthY(mm) })),
    xTicks: xTicksOf(days, gaps),
    baselineY: LENGTH_BOTTOM,
  };
}

/** Pain bars: a reported 0 is an empty bar slot; an unreported day (`null`) is a gap, never a zero. */
export function buildPainChart(days: readonly DayRecord[]): PainChart {
  const bars = days.flatMap((d, i) =>
    d.pain === null
      ? []
      : [{ day: d.day, x: centreX(i, days.length) - BAR_WIDTH / 2, y: PAIN_BASELINE - d.pain * PAIN_UNIT, height: d.pain * PAIN_UNIT, width: BAR_WIDTH }],
  );
  const gaps = gapsOf(days, (d) => d.pain === null);
  return {
    bars,
    gaps,
    xTicks: xTicksOf(days, gaps),
    baselineY: PAIN_BASELINE,
    yTicks: [10, 5, 0].map((value) => ({ value, y: PAIN_BASELINE - value * PAIN_UNIT })),
  };
}

export const PAIN_PLOT = { top: PAIN_TOP, bottom: PAIN_BASELINE };
export const LENGTH_PLOT = { top: PLOT_TOP, bottom: LENGTH_BOTTOM };

/** Day-strip axis: the first day, every seventh day and the last one ("Today, 87" when it is today). */
export function dayAxisTicks(days: readonly DayRecord[], todayDay: number): { label: string; percent: number; isToday: boolean }[] {
  const last = days.length - 1;
  return days.flatMap((d, i) => {
    if (i % 7 !== 0 && i !== last) return [];
    if (i !== last && last - i < 3) return [];
    const isToday = i === last && d.day === todayDay;
    return [{ label: isToday ? `Today, ${String(d.day)}` : `Day ${String(d.day)}`, percent: ((i + 0.5) / days.length) * 100, isToday }];
  });
}

export interface Spot {
  dot: number;
  bandLeft: number | null;
  bandWidth: number | null;
}

/** Position of a value (and the optional reference band) on a gauge, as percentages clamped to the scale. */
export function spotPosition(value: number, lo: number, hi: number, band: { lo: number; hi: number } | null): Spot {
  const pos = (v: number): number => clamp(((v - lo) / (hi - lo)) * 100, 0, 100);
  return band === null
    ? { dot: pos(value), bandLeft: null, bandWidth: null }
    : { dot: pos(value), bandLeft: pos(band.lo), bandWidth: pos(band.hi) - pos(band.lo) };
}

export interface Delta {
  direction: 'up' | 'down' | 'same';
  amount: number;
}

/** Change against the previous day with data, judged at the displayed precision; `null` when there is nothing to compare. */
export function deltaBetween(now: number, before: number | null, digits: number): Delta | null {
  if (before === null) return null;
  const amount = Number(Math.abs(now - before).toFixed(digits));
  if (amount === 0) return { direction: 'same', amount: 0 };
  return { direction: now > before ? 'up' : 'down', amount };
}
