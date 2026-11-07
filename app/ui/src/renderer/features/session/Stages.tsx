import { useEffect, useState } from 'react';

import { Button } from '../../components/ui/Button';
import { Panel } from '../../components/ui/Panel';
import { useData } from '../../data/DataContext';
import type { RepState, SessionData } from '../../data/types';
import { stages } from '../../strings/session';
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
        {session.fit.items.map((item) => (
          <li key={item.id} className={styles.fitRow}>
            <span className={styles.tick} aria-hidden="true">
              ✓
            </span>
            <span>
              <b>{item.label}</b> {item.detail}
            </span>
          </li>
        ))}
      </ul>
      <p className={styles.doneNote}>{copy.done(session.fit.fittedGmt)}</p>
    </Panel>
  );
}

export function CheckStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.check;
  const { continuedWithoutGmt } = session.checks;
  return (
    <Panel className={styles.stage}>
      <h2>{copy.title}</h2>
      <p className={styles.stageLede}>{copy.lede}</p>
      <ul className={styles.rows}>
        {session.checks.items.map((check) => (
          <li key={check.sensor} className={styles.checkRow}>
            <span className={styles.badge} data-status={check.status}>
              {check.status === 'good' ? copy.good : copy.weak}
            </span>
            <b>{check.sensor}</b>
            <span className={styles.muted}>{check.detail}</span>
          </li>
        ))}
      </ul>
      <div className={styles.actions}>
        <Button variant="quiet">{copy.again}</Button>
        <Button variant="quiet">{copy.without}</Button>
      </div>
      <p className={styles.hint}>{copy.hint}</p>
      {continuedWithoutGmt !== null && <p className={styles.doneNote}>{copy.done(continuedWithoutGmt)}</p>}
    </Panel>
  );
}

export function CalibrateStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.mvc;
  const pulls = Array.from({ length: session.calibration.pulls }, (_, i) => i + 1);
  return (
    <Panel className={styles.stage}>
      <h2>{copy.title}</h2>
      <p className={styles.stageLede}>{copy.lede}</p>
      <ol className={styles.rows}>
        {pulls.map((n) => (
          <li key={n} className={styles.trialRow}>
            <b>{copy.pull(n)}</b>
            <span className={styles.muted}>{copy.recorded}</span>
          </li>
        ))}
      </ol>
      <p className={styles.doneNote}>{copy.done(session.calibration.savedGmt, session.calibration.note)}</p>
    </Panel>
  );
}

/** Live rep counter fed by the DataSource (the harness IMU, 450 ms); unsubscribes on unmount. */
export function RepsStage({ session }: { session: SessionData }): React.JSX.Element {
  const copy = stages.reps;
  const data = useData();
  const [state, setState] = useState<RepState | null>(null);
  useEffect(() => data.subscribeReps(setState), [data]);
  return (
    <Panel className={styles.stage} aria-live="polite">
      <div className={styles.repLine}>
        <span className={styles.repCount}>
          {state?.rep} <small>{copy.of(state?.total ?? session.totalReps)}</small>
        </span>
        <span className={styles.phase}>{state?.phase === 'letting_up' ? copy.lettingUp : copy.pulling}</span>
      </div>
      <div
        role="progressbar"
        aria-label={copy.progress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={state?.barPercent ?? 0}
        className={styles.bar}
      >
        <span style={{ width: `${String(state?.barPercent ?? 0)}%` }} />
      </div>
      <dl className={styles.detect}>
        <dt>{copy.countingBy}</dt>
        <dd>{session.countingBy}</dd>
        <dt>{copy.lastRep}</dt>
        <dd>{state?.last ? copy.last(state.last.rep, state.last.seconds) : copy.noLast}</dd>
        <dt>{copy.backup}</dt>
        <dd>{session.backup}</dd>
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
      <div className={styles.repLine}>
        <span className={styles.repCount} data-size="small">
          {formatRest(session.restSeconds)}
        </span>
        <span className={styles.phase}>{copy.resting}</span>
      </div>
      <div className={styles.actions}>
        <Button disabled>{copy.finish}</Button>
      </div>
      <p className={styles.hint}>{copy.hint}</p>
    </Panel>
  );
}
