import { useState } from 'react';

import { Button } from '../../components/ui/Button';
import { Panel } from '../../components/ui/Panel';
import { cx } from '../../components/ui/cx';
import type { DataSource } from '../../data/DataSource';
import type { DayLevel, DayRecord, HistoryData } from '../../data/types';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { levelLabel } from '../../strings/common';
import { changeWhen, dayNote, daysSummary, history, showDayLabel } from '../../strings/history';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { LengthChartFigure, PainChartFigure } from './Charts';
import { DayDetail } from './DayDetail';
import { DayStrip, type DayFilter } from './DayStrip';
import styles from './History.module.css';

const selectHistory = (data: DataSource): Promise<HistoryData> => data.getHistory();

const FILTERS: readonly { value: DayFilter; label: string }[] = [
  { value: 'all', label: history.filterAll },
  { value: 'amber', label: history.filterAmber },
  { value: 'green', label: history.filterGreen },
  { value: 'nodata', label: history.filterNoData },
];

const SWATCH: Record<DayLevel, string | undefined> = {
  green: styles.green,
  amber: styles.amber,
  red: styles.red,
  nodata: styles.nodata,
};

const countOf = (days: readonly DayRecord[], filter: DayFilter): number =>
  filter === 'all' ? days.length : days.filter((d) => d.level === filter).length;

interface Change {
  first: DayRecord;
  last: DayRecord;
  isFirstRun: boolean;
}

/** Consecutive days with the same level, newest first. */
function changesOf(days: readonly DayRecord[]): Change[] {
  const runs: Change[] = [];
  days.forEach((d, i) => {
    const run = runs.at(-1);
    if (run && days[i - 1]?.level === d.level) run.last = d;
    else runs.push({ first: d, last: d, isFirstRun: i === 0 });
  });
  return runs.reverse();
}

function Changes({ days, onShow }: { days: readonly DayRecord[]; onShow: (date: string) => void }): React.JSX.Element {
  return (
    <Panel as="section" aria-labelledby="history-changes-title" className={styles.card}>
      <h2 id="history-changes-title">{history.changesTitle}</h2>
      <ol className={styles.changes} aria-labelledby="history-changes-title">
        {changesOf(days).map(({ first, last, isFirstRun }) => (
          <li key={first.date}>
            <span className={cx(styles.node, SWATCH[first.level])} aria-hidden="true" />
            <span className={styles.when}>{changeWhen(first, last, isFirstRun, first.level)}</span>
            <span>
              <span className={cx(styles.tag, SWATCH[first.level])}>{levelLabel(first.level)}</span>. {dayNote(first.level)}
            </span>
            <Button
              variant="quiet"
              aria-label={showDayLabel(first)}
              onClick={() => { onShow(first.date); }}
            >
              {history.showDay}
            </Button>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

function HistoryBody({ data }: { data: HistoryData }): React.JSX.Element {
  const { days, todayDay, preflightRangeMm } = data;
  const [selectedDay, setSelectedDay] = useState(() => (days.find((d) => d.day !== null && d.day === todayDay) ?? days[days.length - 1])?.date ?? '');
  const [filter, setFilter] = useState<DayFilter>('all');
  const selectedIndex = Math.max(0, days.findIndex((d) => d.date === selectedDay));
  const selected = days[selectedIndex];
  const previous = days.slice(0, selectedIndex).reverse().find((d) => d.level !== 'nodata') ?? null;
  return (
    <>
      <p className={styles.lede}>{history.lede}</p>
      <LengthChartFigure days={days} range={preflightRangeMm} />
      <PainChartFigure days={days} />
      <Panel as="section" aria-labelledby="history-days-title" className={styles.card}>
        <div className={styles.daysHead}>
          <div>
            <h2 id="history-days-title" className={styles.flush}>{history.daysTitle}</h2>
            <p className={styles.daysSummary}>
              {daysSummary(countOf(days, 'amber'), countOf(days, 'nodata'), countOf(days, 'green'))}
            </p>
          </div>
          <div className={styles.filters} role="group" aria-label={history.filterGroup}>
            {FILTERS.map((f) => (
              <button key={f.value} type="button" className={styles.pick} aria-pressed={filter === f.value} onClick={() => { setFilter(f.value); }}>
                {f.value !== 'all' && <i className={cx(styles.sw, SWATCH[f.value])} aria-hidden="true" />}
                {f.label} <span>{countOf(days, f.value)}</span>
              </button>
            ))}
          </div>
        </div>
        <DayStrip days={days} selectedDay={selectedDay} todayDay={todayDay} filter={filter} onSelect={setSelectedDay} />
        {selected ? <DayDetail day={selected} previous={previous} todayDay={todayDay} preflightRangeMm={preflightRangeMm} /> : null}
      </Panel>
      <Changes days={days} onShow={setSelectedDay} />
    </>
  );
}

export function HistoryView(): React.JSX.Element {
  const { data, error } = useLoad(selectHistory, { refreshMs: REFRESH_MS });
  return (
    <section aria-labelledby="history-title" data-testid="history-view">
      <h1 id="history-title">{history.title}</h1>
      {error ? <LoadFailure error={error} fallback={history.loadFailed} /> : null}
      {!data && !error ? <p role="status">{history.loading}</p> : null}
      {data ? <HistoryBody data={data} /> : null}
    </section>
  );
}
