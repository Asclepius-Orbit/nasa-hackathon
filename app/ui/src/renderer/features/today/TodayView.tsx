import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';

import { Button } from '../../components/ui/Button';
import { cx } from '../../components/ui/cx';
import { Collapsible } from '../../components/ui/Collapsible';
import { useData } from '../../data/DataContext';
import type { DataSource } from '../../data/DataSource';
import type { TodayData } from '../../data/types';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { useNow } from '../../clock/Clock';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { recommendationText, missingReasonText, reasonText, type PlanText } from '../../strings/codes';
import { levelLabel, urgentButton } from '../../strings/common';
import {
  confidenceValue,
  inProgressLine,
  inProgressSince,
  minutesAgo,
  spine,
  today as copy,
} from '../../strings/today';
import { useUrgent } from '../urgent/UrgentContext';
import { gmtClock, gmtDay, isSameGmtDay, minutesSince } from './gmt';
import { SpineFigure } from './SpineFigure';
import styles from './TodayView.module.css';
import { TodoRows } from './TodoRows';

const selectToday = (d: DataSource): ReturnType<DataSource['getToday']> => d.getToday();
const selectCatalog = (d: DataSource): ReturnType<DataSource['getCatalog']> => d.getCatalog();

/**
 * Live rep of the running session, `null` until the data source reports one and whenever it is not live (no signal,
 * lost, no counter): then the verdict falls back to the line without a count. Unsubscribes on unmount.
 */
function useLiveRep(sessionId: string | null): number | null {
  const source = useData();
  const [rep, setRep] = useState<number | null>(null);
  useEffect(() => {
    if (sessionId === null) return undefined;
    return source.subscribeReps(sessionId, (update) => {
      setRep(update.kind === 'state' && update.state.signal === 'ok' && update.state.rep > 0 ? update.state.rep : null);
    });
  }, [source, sessionId]);
  return sessionId === null ? null : rep;
}

function PlanRow({ item, extra }: { item: PlanText; extra: string | null }): React.JSX.Element {
  const navigate = useNavigate();
  const { action } = item;
  const detail = extra === null ? item.detail : `${extra} ${item.detail}`.trim();
  return (
    <li>
      <span>
        {item.title}
        {detail !== '' && <small>{detail}</small>}
      </span>
      {action !== null && (
        <Button
          variant={action.kind}
          disabled={action.route === null}
          onClick={action.route === null ? undefined : () => void navigate(action.route ?? '')}
        >
          {action.label}
        </Button>
      )}
    </li>
  );
}

const SESSION_ROUTE = '/session';

function Verdict({ today, liveRep }: { today: TodayData; liveRep: number | null }): React.JSX.Element {
  const running = today.sessionInProgress;
  const plan = today.recommendations.map((code) => ({ code, item: recommendationText(code) }));
  const sessionInPlan = plan.some(({ item }) => item.action?.route === SESSION_ROUTE);
  const runningLine = (): string | null =>
    running === null
      ? null
      : liveRep === null
        ? inProgressSince(gmtClock(running.startedAt), running.totalReps)
        : inProgressLine(gmtClock(running.startedAt), liveRep, running.totalReps);
  const reportDue = today.alsoToday.includes('TASK_DAILY_REPORT');
  const todoNow = today.level === 'amber' ? copy.todoNow.amber(plan.length, reportDue) : today.level === 'green' ? copy.todoNow.green(reportDue) : copy.todoNow[today.level];
  return (
    <div className={styles.verdict}>
      <h1 id="today-title" className={styles.level} data-level={today.level}>
        {levelLabel(today.level)}
      </h1>
      <p className={styles.meaning}>{copy.verdicts[today.level]}</p>
      <p className={styles.todo}>
        <b>{copy.whatToDo}</b> {todoNow}
      </p>
      <ul className={styles.plan} aria-label={copy.planLabel}>
        {plan.map(({ code, item }) => (
          <PlanRow key={code} item={item} extra={item.action?.route === SESSION_ROUTE ? runningLine() : null} />
        ))}
        {running !== null && !sessionInPlan && (
          <PlanRow
            item={{
              title: copy.sessionRunning,
              detail: copy.sessionRunningDetail,
              action: { label: copy.openSession, route: SESSION_ROUTE, kind: 'primary' },
            }}
            extra={runningLine()}
          />
        )}
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
  const now = useNow();
  return (
    <div className={cx(styles.card, styles.why)}>
      <h2>{copy.why(today.level)}</h2>
      {today.reasons.length > 0 && (
        <ul className={styles.reasons}>
          {today.reasons.map((reason) => {
            const text = reasonText(reason.featureId, reason.deviationZ);
            return (
              <li key={reason.featureId}>
                <strong>{text.label}</strong>: {text.text}
              </li>
            );
          })}
        </ul>
      )}
      {trust === null ? (
        <p>{copy.noAssessment}</p>
      ) : (
        <div className={styles.trust}>
          <div>
            <span>{copy.confidence}</span>
            <b>{confidenceValue(trust.confidencePct)}</b>
            <span>{copy.confidenceNote}</span>
          </div>
          <div>
            <span>{copy.dataAvailable}</span>
            <b>{copy.dataOf(trust.dataAvailable, trust.dataTotal)}</b>
            {trust.missing.length > 0 && <span>{copy.missing(trust.missing.map((m) => missingReasonText(m.what, m.reason)).join('; '))}</span>}
          </div>
          <div>
            <span>{copy.assessed}</span>
            <b>{minutesAgo(minutesSince(trust.assessedAt, now))}</b>
            <span>{copy.assessedNote}</span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Which day the lumbar length was measured, but only when it is not today: an old measure must not pass for a fresh one. */
function measuredNote(today: TodayData, now: Date): string | null {
  const at = today.lumbarMeasuredAt;
  return today.lumbarLengthMm === null || at === null || isSameGmtDay(at, now) ? null : spine.measured(gmtDay(at));
}

/** Today: the spine figure, the verdict with its plan, why, and what else is due. */
export function TodayView(): React.JSX.Element {
  const { data, error } = useLoad(selectToday, { refreshMs: REFRESH_MS });
  const liveRep = useLiveRep(data?.sessionInProgress?.sessionId ?? null);
  const now = useNow();
  return (
    <section aria-labelledby="today-title" data-testid="today-view">
      {data === null ? (
        <>
          <h1 id="today-title">{copy.title}</h1>
          {error === null ? <p role="status">{copy.loading}</p> : <LoadFailure error={error} fallback={copy.error} />}
        </>
      ) : (
        <div className={styles.today}>
          <SpineFigure lumbarLengthMm={data.lumbarLengthMm} measuredNote={measuredNote(data, now)} />
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
