import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';

import { Button } from '../../components/ui/Button';
import { cx } from '../../components/ui/cx';
import { Collapsible } from '../../components/ui/Collapsible';
import { useData } from '../../data/DataContext';
import type { DataSource } from '../../data/DataSource';
import type { PlanItem, TodayData } from '../../data/types';
import { useLoad } from '../../data/useLoad';
import { levelLabel, urgentButton } from '../../strings/common';
import {
  confidenceValue,
  inProgressLine,
  minutesAgo,
  today as copy,
} from '../../strings/today';
import { useUrgent } from '../urgent/UrgentContext';
import { SpineFigure } from './SpineFigure';
import styles from './TodayView.module.css';
import { TodoRows } from './TodoRows';

const selectToday = (d: DataSource): ReturnType<DataSource['getToday']> => d.getToday();
const selectCatalog = (d: DataSource): ReturnType<DataSource['getCatalog']> => d.getCatalog();

/** Live rep of the running session, `null` until the data source has reported one. */
function useLiveRep(active: boolean): number | null {
  const source = useData();
  const [rep, setRep] = useState<number | null>(null);
  useEffect(() => {
    if (!active) return undefined;
    return source.subscribeReps((state) => {
      setRep(state.rep);
    });
  }, [source, active]);
  return rep;
}

function PlanRow({ item, extra }: { item: PlanItem; extra: string | null }): React.JSX.Element {
  const navigate = useNavigate();
  const { route, label, kind } = item.action;
  return (
    <li>
      <span>
        {item.title}
        <small>{extra === null ? item.detail : `${extra} ${item.detail}`}</small>
      </span>
      <Button
        variant={kind}
        disabled={route === null}
        onClick={route === null ? undefined : () => void navigate(route)}
      >
        {label}
      </Button>
    </li>
  );
}

function Verdict({ today, liveRep }: { today: TodayData; liveRep: number | null }): React.JSX.Element {
  const running = today.sessionInProgress;
  return (
    <div className={styles.verdict}>
      <h1 id="today-title" className={styles.level} data-level={today.level}>
        {levelLabel(today.level)}
      </h1>
      <p className={styles.meaning}>{today.meaning}</p>
      <p className={styles.todo}>
        <b>{copy.whatToDo}</b> {today.whatToDo}
      </p>
      <ul className={styles.plan} aria-label={copy.planLabel}>
        {today.plan.map((item) => (
          <PlanRow
            key={item.id}
            item={item}
            extra={
              running !== null && item.action.route === '/session' && liveRep !== null
                ? inProgressLine(running.sinceGmt, liveRep, running.totalReps)
                : null
            }
          />
        ))}
      </ul>
    </div>
  );
}

function Toggles(): React.JSX.Element {
  const { data: catalog } = useLoad(selectCatalog);
  const urgent = useUrgent();
  return (
    <div className={cx(styles.card, styles.toggles)}>
      <Collapsible tone="urgent" icon="!" summary={copy.warningSummary(catalog?.flags.length ?? 8)}>
        <ol className={styles.catalog}>
          {catalog?.flags.map((flag) => <li key={flag.code}>{flag.label}</li>)}
        </ol>
        <Button
          variant="urgent"
          className={styles.urgentAction}
          onClick={() => {
            urgent.open();
          }}
        >
          {urgentButton}
        </Button>
      </Collapsible>
      <Collapsible icon="?" summary={copy.meaningSummary}>
        <dl className={styles.kv}>
          {(['green', 'amber', 'red'] as const).map((level) => (
            <div key={level} style={{ display: 'contents' }}>
              <dt data-level={level}>{levelLabel(level)}</dt>
              <dd>{copy.meanings[level]}</dd>
            </div>
          ))}
        </dl>
      </Collapsible>
    </div>
  );
}

function Why({ today }: { today: TodayData }): React.JSX.Element {
  const { trust } = today;
  return (
    <div className={cx(styles.card, styles.why)}>
      <h2>{copy.why(today.level)}</h2>
      <ul className={styles.reasons}>
        {today.reasons.map((reason) => (
          <li key={reason.label}>
            <strong>{reason.label}</strong>: {reason.text}
          </li>
        ))}
      </ul>
      <div className={styles.trust}>
        <div>
          <span>{copy.confidence}</span>
          <b>{confidenceValue(trust.confidencePct)}</b>
          <span>{copy.confidenceNote}</span>
        </div>
        <div>
          <span>{copy.dataAvailable}</span>
          <b>{copy.dataOf(trust.dataAvailable, trust.dataTotal)}</b>
          {trust.missing !== '' && <span>{copy.missing(trust.missing)}</span>}
        </div>
        <div>
          <span>{copy.assessed}</span>
          <b>{minutesAgo(trust.assessedMinAgo)}</b>
          <span>{copy.assessedNote}</span>
        </div>
      </div>
    </div>
  );
}

/** Today: the spine figure, the verdict with its plan, why, and what else is due. */
export function TodayView(): React.JSX.Element {
  const { data, error } = useLoad(selectToday);
  const liveRep = useLiveRep(data?.sessionInProgress != null);
  return (
    <section aria-labelledby="today-title" data-testid="today-view">
      {data === null ? (
        <>
          <h1 id="today-title">{copy.title}</h1>
          {error === null ? <p role="status">{copy.loading}</p> : <p role="alert">{copy.error}</p>}
        </>
      ) : (
        <div className={styles.today}>
          <SpineFigure lumbarLengthMm={data.lumbarLengthMm} />
          <div>
            <Verdict today={data} liveRep={liveRep} />
            <Toggles />
            <Why today={data} />
            <h2>{copy.alsoToday}</h2>
            <TodoRows items={data.alsoToday} />
          </div>
        </div>
      )}
    </section>
  );
}
