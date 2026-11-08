import { useEffect, useState } from 'react';

import { Button } from '../../components/ui/Button';
import { Panel } from '../../components/ui/Panel';
import { useData } from '../../data/DataContext';
import type { RepState, SessionData } from '../../data/types';
import { checkDetail, fitItemText, streamLabel } from '../../strings/codes';
import { session as sessionCopy, stages } from '../../strings/session';
import { gmtClock } from '../today/gmt';
import styles from './SessionView.module.css';

export type StageId = SessionData['steps'][number]['id'];

export const STAGE_IDS: readonly StageId[] = ['fit', 'check', 'mvc', 'reps', 'finish'];

export function FitStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.fit;
  return (
    <Panel className={styles.stage}>
      <h2>{copy.title}</h2>
      <p className={styles.stageLede}>{copy.lede}</p>
      <ul className={styles.rows}>
        {session.fitItems.map((id) => {
          const item = fitItemText(id);
          return (
            <li key={id} className={styles.fitRow}>
              <span className={styles.tick} aria-hidden="true">
                ✓
              </span>
              <span>
                <b>{item.label}</b> {item.detail}
              </span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

export function CheckStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.check;
  return (
    <Panel className={styles.stage}>
      <h2>{copy.title}</h2>
      <p className={styles.stageLede}>{copy.lede}</p>
      <ul className={styles.rows}>
        {session.sensorChecks.map((check) => (
          <li key={check.stream} className={styles.checkRow}>
            <span className={styles.badge} data-status={check.status}>
              {check.status === 'good' ? copy.good : copy.weak}
            </span>
            <b>{streamLabel(check.stream)}</b>
            <span className={styles.muted}>{checkDetail(check.status, check.state)}</span>
          </li>
        ))}
      </ul>
      {session.sensorChecks.length === 0 && <p className={styles.hint}>{copy.none}</p>}
      <div className={styles.actions}>
        <Button variant="quiet">{copy.again}</Button>
        <Button variant="quiet">{copy.without}</Button>
      </div>
      <p className={styles.hint}>{copy.hint}</p>
    </Panel>
  );
}

export function CalibrateStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.mvc;
  const { calibration } = session;
  return (
    <Panel className={styles.stage}>
      <h2>{copy.title}</h2>
      <p className={styles.stageLede}>{copy.lede}</p>
      <p className={styles.doneNote}>
        {calibration === null
          ? copy.notSent
          : calibration.acked && calibration.ackedAt !== null
            ? copy.saved(gmtClock(calibration.ackedAt))
            : copy.waiting}
      </p>
    </Panel>
  );
}

/** Live rep counter fed by the DataSource (the harness IMU, 450 ms); unsubscribes on unmount. */
export function RepsStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.reps;
  if (session.totalReps === null) {
    return (
      <Panel className={styles.stage}>
        <p>{copy.noReps}</p>
      </Panel>
    );
  }
  return <CountedReps />;
}

function CountedReps(): React.JSX.Element {
  const copy = stages.reps;
  const data = useData();
  const [state, setState] = useState<RepState | null>(null);
  useEffect(() => data.subscribeReps(setState), [data]);
  // No state means no live signal (real mode until issue #61): say so instead of drawing a counter.
  if (state === null) {
    return (
      <Panel className={styles.stage} aria-live="polite">
        <p>{copy.noSignal}</p>
      </Panel>
    );
  }
  return (
    <Panel className={styles.stage} aria-live="polite">
      <div className={styles.repLine}>
        <span className={styles.repCount}>
          {state.rep} <small>{copy.of(state.total)}</small>
        </span>
        <span className={styles.phase}>{state.phase === 'letting_up' ? copy.lettingUp : copy.pulling}</span>
      </div>
      <div
        role="progressbar"
        aria-label={copy.progress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={state.barPercent}
        className={styles.bar}
      >
        <span style={{ width: `${String(state.barPercent)}%` }} />
      </div>
      <dl className={styles.detect}>
        <dt>{copy.countingBy}</dt>
        <dd>{sessionCopy.countingBy}</dd>
        <dt>{copy.lastRep}</dt>
        <dd>{state.last ? copy.last(state.last.rep, state.last.seconds) : copy.noLast}</dd>
        <dt>{copy.backup}</dt>
        <dd>{sessionCopy.backup}</dd>
      </dl>
      <div className={styles.minor}>
        <Button variant="quiet">{copy.artefact}</Button>
        <Button variant="quiet">{copy.recount}</Button>
      </div>
    </Panel>
  );
}

function formatRest(seconds: number): string {
  return `${String(Math.floor(seconds / 60))}:${String(seconds % 60).padStart(2, '0')}`;
}

export function FinishStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.finish;
  return (
    <Panel className={styles.stage}>
      <h2>{copy.title}</h2>
      <p className={styles.stageLede}>{copy.lede}</p>
      {session.restSeconds === null ? (
        <p className={styles.hint}>{copy.noRest}</p>
      ) : (
        <div className={styles.repLine}>
          <span className={styles.repCount} data-size="small">
            {formatRest(session.restSeconds)}
          </span>
          <span className={styles.phase}>{copy.resting}</span>
        </div>
      )}
      <div className={styles.actions}>
        <Button disabled>{copy.finish}</Button>
      </div>
      <p className={styles.hint}>{copy.hint}</p>
    </Panel>
  );
}
