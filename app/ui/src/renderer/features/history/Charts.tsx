import { useId } from 'react';

import type { DayRecord } from '../../data/types';
import { useSettings } from '../../settings/SettingsProvider';
import { history, lengthChartLabel, painChartLabel } from '../../strings/history';
import styles from './Charts.module.css';
import { buildLengthChart, buildPainChart, LENGTH_PLOT, PAIN_PLOT, type Gap, type XTick } from './scales';

const VIEW_WIDTH = 960;
const AXIS_START = 60;
const AXIS_END = 940;
const LABEL_BELOW = 22;
const Y_LABEL_DROP = 5;
const TOP_LABEL_DROP = 8;

const useHatchId = (): string => `hatch-${useId().replace(/:/g, '')}`;

function Hatch({ id }: { id: string }): React.JSX.Element {
  return (
    <defs>
      <pattern id={id} width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="12" height="12" className={styles.hatchBase} />
        <rect width="6" height="12" className={styles.hatchStripe} />
      </pattern>
    </defs>
  );
}

function GapRects({ gaps, top, bottom, hatchId }: { gaps: readonly Gap[]; top: number; bottom: number; hatchId: string }): React.JSX.Element {
  return (
    <>
      {gaps.map((g) => (
        <rect key={g.label} data-testid="nodata-gap" x={g.x} y={top} width={g.width} height={bottom - top} fill={`url(#${hatchId})`} />
      ))}
    </>
  );
}

function XLabels({ ticks, gaps, y }: { ticks: readonly XTick[]; gaps: readonly Gap[]; y: number }): React.JSX.Element {
  return (
    <g className={styles.label}>
      {ticks.map((t) => (
        <text key={t.label} x={t.x} y={y} textAnchor={t.anchor}>
          {t.label}
        </text>
      ))}
      {gaps.map((g) => (
        <text key={g.label} x={g.labelX} y={y} textAnchor="middle">
          {g.label}
        </text>
      ))}
    </g>
  );
}

const firstDay = (days: readonly DayRecord[]): number => days[0]?.day ?? 0;
const lastDay = (days: readonly DayRecord[]): number => days[days.length - 1]?.day ?? 0;

export function LengthChartFigure({ days, range }: { days: readonly DayRecord[]; range: { lo: number; hi: number } }): React.JSX.Element {
  const { formatLength } = useSettings();
  const hatchId = useHatchId();
  const chart = buildLengthChart(days, range);
  const latestDay = [...days].reverse().find((d) => d.lengthMm !== null);
  const latestMm = latestDay?.lengthMm ?? 0;
  const label = lengthChartLabel({
    first: firstDay(days),
    last: lastDay(days),
    latest: latestDay
      ? { day: latestDay.day, text: formatLength(latestMm), position: latestMm > range.hi ? 'above' : latestMm < range.lo ? 'below' : 'inside' }
      : null,
    rangeText: `${formatLength(range.lo)} to ${formatLength(range.hi)}`,
    gapLabels: chart.gaps.map((g) => g.label),
  });
  return (
    <figure className={styles.chart}>
      <h2>{history.lengthTitle}</h2>
      <p className={styles.sub}>{history.lengthSub}</p>
      <div className={styles.legend}>
        <span>
          <i className={styles.swatchRange} />
          {history.legendRange}
        </span>
        <span>
          <i className={styles.swatchValue} />
          {history.legendValue}
        </span>
        <span>
          <i className={styles.swatchNoData} />
          {history.legendNoData}
        </span>
      </div>
      <svg viewBox={`0 0 ${String(VIEW_WIDTH)} 220`} role="img" aria-label={label}>
        <Hatch id={hatchId} />
        <GapRects gaps={chart.gaps} top={LENGTH_PLOT.top} bottom={LENGTH_PLOT.bottom} hatchId={hatchId} />
        <rect x={AXIS_START} y={chart.band.y} width={AXIS_END - AXIS_START} height={chart.band.height} className={styles.band} />
        <line x1={AXIS_START} y1={chart.baselineY} x2={AXIS_END} y2={chart.baselineY} className={styles.axis} />
        <g className={styles.label}>
          {chart.yTicks.map((t) => (
            <text key={t.mm} x={0} y={t.y + Y_LABEL_DROP}>
              {formatLength(t.mm)}
            </text>
          ))}
        </g>
        <XLabels ticks={chart.xTicks} gaps={chart.gaps} y={chart.baselineY + LABEL_BELOW} />
        {chart.segments.map((s) => (
          <polyline
            key={s.points[0]?.day}
            data-testid="length-segment"
            className={styles.line}
            points={s.points.map((p) => `${String(p.x)},${String(p.y)}`).join(' ')}
          />
        ))}
        <g className={styles.dots}>
          {chart.segments.map((s) => {
            const end = s.points.at(-1);
            return end ? <circle key={end.day} cx={end.x} cy={end.y} r={6} /> : null;
          })}
        </g>
      </svg>
    </figure>
  );
}

export function PainChartFigure({ days }: { days: readonly DayRecord[] }): React.JSX.Element {
  const hatchId = useHatchId();
  const chart = buildPainChart(days);
  const latest = [...days].reverse().find((d) => d.pain !== null);
  const label = painChartLabel({
    first: firstDay(days),
    last: lastDay(days),
    latest: latest ? { day: latest.day, value: latest.pain ?? 0 } : null,
    gapLabels: chart.gaps.map((g) => g.label),
  });
  return (
    <figure className={styles.chart}>
      <h2>{history.painTitle}</h2>
      <p className={styles.sub}>{history.painSub}</p>
      <svg viewBox={`0 0 ${String(VIEW_WIDTH)} 142`} role="img" aria-label={label}>
        <Hatch id={hatchId} />
        <GapRects gaps={chart.gaps} top={PAIN_PLOT.top} bottom={PAIN_PLOT.bottom} hatchId={hatchId} />
        <line x1={AXIS_START} y1={chart.baselineY} x2={AXIS_END} y2={chart.baselineY} className={styles.axis} />
        <g className={styles.label}>
          {chart.yTicks.map((t) => (
            <text key={t.value} x={0} y={t.y + (t.value === 10 ? TOP_LABEL_DROP : Y_LABEL_DROP)}>
              {t.value}
            </text>
          ))}
        </g>
        <XLabels ticks={chart.xTicks} gaps={chart.gaps} y={chart.baselineY + LABEL_BELOW} />
        <g className={styles.bars}>
          {chart.bars.map((b) => (
            <rect key={b.day} data-testid="pain-bar" x={b.x} y={b.y} width={b.width} height={b.height} rx={3} />
          ))}
        </g>
      </svg>
    </figure>
  );
}
