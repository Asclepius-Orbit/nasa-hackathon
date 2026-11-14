import { NoData } from '../../components/ui/Panel';
import { cx } from '../../components/ui/cx';
import type { DayLevel, DayRecord } from '../../data/types';
import { useSettings } from '../../settings/SettingsProvider';
import { levelLabel } from '../../strings/common';
import { dayHeading, dayNote, deltaText, history, lengthGauge, painOfTen, reportLine, sessionLine } from '../../strings/history';
import styles from './History.module.css';
import { deltaBetween, spotPosition } from './scales';

const LEVEL_CLASS: Record<DayLevel, string | undefined> = {
  green: styles.green,
  amber: styles.amber,
  red: styles.red,
  nodata: undefined,
};

interface SpotProps {
  value: number;
  lo: number;
  hi: number;
  band: { lo: number; hi: number } | null;
  loLabel: string;
  hiLabel: string;
}

/** Gauge with the value as a dot on a track, and optionally the reference band behind it. */
function Spot({ value, lo, hi, band, loLabel, hiLabel }: SpotProps): React.JSX.Element {
  const pos = spotPosition(value, lo, hi, band);
  return (
    <div className={styles.spot} aria-hidden="true">
      <div className={styles.spotBar}>
        {pos.bandLeft !== null && pos.bandWidth !== null ? (
          <span className={styles.band} style={{ left: `${String(pos.bandLeft)}%`, width: `${String(pos.bandWidth)}%` }} />
        ) : null}
        <span className={styles.dot} style={{ left: `${String(pos.dot)}%` }} />
      </div>
      <div className={styles.spotEnds}>
        <span>{loLabel}</span>
        <span>{hiLabel}</span>
      </div>
    </div>
  );
}

export interface DayDetailProps {
  day: DayRecord;
  /** Previous day that has data, to compare with. */
  previous: DayRecord | null;
  todayDay: number | null;
  /** `null` without a preflight reference: the gauge then shows the value alone. */
  preflightRangeMm: { lo: number; hi: number } | null;
}

export function DayDetail({ day, previous, todayDay, preflightRangeMm }: DayDetailProps): React.JSX.Element {
  const { formatLength } = useSettings();
  const noData = day.level === 'nodata';
  const unsigned = (text: string): string => text.replace(/^[+−]/, '');

  const lengthDelta = day.lengthMm === null || previous === null ? null : deltaBetween(day.lengthMm, previous.lengthMm, 1);
  const painDelta = day.pain === null || previous === null ? null : deltaBetween(day.pain, previous.pain, 0);
  return (
    <div className={styles.dayDetail} data-testid="day-detail" aria-live="polite">
      <div>
        <div className={styles.big}>
          {dayHeading(day)}
          {day.day !== null && day.day === todayDay ? <span className={styles.todayNote}> {history.today}</span> : null}
        </div>
        <div className={cx(styles.lvl, styles.tag, LEVEL_CLASS[day.level])}>{noData ? <NoData /> : levelLabel(day.level)}</div>
      </div>
      <div>
        <div className={styles.facts}>
          <div>
            {history.lumbarLength}
            {day.lengthMm === null ? (
              <b><NoData /></b>
            ) : (
              <>
                <b>{formatLength(day.lengthMm)}</b>
                <Spot value={day.lengthMm} lo={lengthGauge.lo} hi={lengthGauge.hi} band={preflightRangeMm} loLabel={formatLength(lengthGauge.lo)} hiLabel={formatLength(lengthGauge.hi)} />
                {lengthDelta && previous ? (
                  <span className={styles.delta}>
                    {deltaText(lengthDelta.direction, unsigned(formatLength(lengthDelta.amount)), previous)}
                  </span>
                ) : null}
              </>
            )}
          </div>
          <div>
            {history.pain}
            {day.pain === null ? (
              <b><NoData /></b>
            ) : (
              <>
                <b>{painOfTen(day.pain)}</b>
                <Spot value={day.pain} lo={0} hi={10} band={null} loLabel="0" hiLabel="10" />
                {painDelta && previous ? (
                  <span className={styles.delta}>{deltaText(painDelta.direction, String(painDelta.amount), previous)}</span>
                ) : null}
              </>
            )}
          </div>
          <div>
            {history.dailyReport}
            <b>{reportLine(day.report)}</b>
          </div>
        </div>
        <p>
          <b>{history.session}</b> {sessionLine(day.session)}. {dayNote(day.level)}
        </p>
      </div>
    </div>
  );
}
