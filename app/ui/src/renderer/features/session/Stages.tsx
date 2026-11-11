import { Button } from '../../components/ui/Button';
import { useSessionRecheck } from '../../components/layout/NoMonitor';
import { Panel } from '../../components/ui/Panel';
import type { RepsUpdate, SessionData } from '../../data/types';
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

export interface StageAction {
  /** The session is running, so the harness can take the action. */
  enabled: boolean;
  /** Another action is on its way. */
  busy: boolean;
}

export function CalibrateStage({ session, action, onCalibrate }: { session: SessionData; action: StageAction; onCalibrate: () => void }): React.JSX.Element {
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
      <div className={styles.actions}>
        <Button disabled={!action.enabled || action.busy} onClick={onCalibrate}>
          {copy.calibrate}
        </Button>
      </div>
    </Panel>
  );
}

/** Live rep counter fed by the DataSource (the harness IMU, 450 ms); unsubscribes on unmount. */
export function RepsStage({
  session,
  update,
  action,
  onArtifact,
}: {
  session: SessionData;
  update: RepsUpdate | null;
  action: StageAction;
  onArtifact: () => void;
}): React.JSX.Element {
  const copy = stages.reps;
  if (session.totalReps === null) {
    return (
      <Panel className={styles.stage}>
        <p>{copy.noReps}</p>
      </Panel>
    );
  }
  return <CountedReps update={update} action={action} onArtifact={onArtifact} />;
}

function CountedReps({ update, action, onArtifact }: { update: RepsUpdate | null; action: StageAction; onArtifact: () => void }): React.JSX.Element {
  const copy = stages.reps;
  useSessionRecheck(update?.kind === 'locked' ? new Error('locked') : null);
  if (update?.kind === 'unavailable') {
    return (
      <Panel className={styles.stage} role="status" aria-live="polite">
        <p className={styles.repAlert}>{copy.unavailable}</p>
        <p className={styles.hint}>{copy.unavailableHint}</p>
      </Panel>
    );
  }
  if (update?.kind === 'reconnecting') {
    // The stream dropped before any count arrived: say so, and draw no number.
    return (
      <Panel className={styles.stage} role="status" aria-live="polite">
        <div className={styles.repAlert}>
          <p>{copy.lost}</p>
          <p className={styles.hint}>{copy.lostNoCountHint}</p>
        </div>
      </Panel>
    );
  }
  // No state yet, no reps, unknown session or locked: no counter is drawn, never a made-up one.
  if (update?.kind !== 'state') {
    return (
      <Panel className={styles.stage} aria-live="polite">
        <p>{copy.noCount}</p>
      </Panel>
    );
  }
  const { state } = update;
  const ended = state.signal === 'ended';
  const live = state.signal === 'ok';
  const phaseText = { pulling: copy.pulling, letting_up: copy.lettingUp, resting: copy.resting } as const;
  // A rep past the planned total ("11 of 10") would contradict itself: the number stands alone.
  const total = state.total !== null && state.rep <= state.total ? state.total : null;
  return (
    <Panel className={styles.stage} aria-live="polite" role={live || ended ? undefined : 'status'}>
      {!live && !ended && (
        <div className={styles.repAlert}>
          <p>{state.signal === 'lost' ? copy.lost : copy.noSignal}</p>
          <p className={styles.hint}>{state.signal === 'lost' ? copy.lostHint : copy.noSignalHint}</p>
        </div>
      )}
      <div className={styles.repLine} data-live={live}>
        <span className={styles.repCount}>
          {state.rep}
          {total !== null && (
            <>
              {' '}
              <small>{copy.of(total)}</small>
            </>
          )}
        </span>
        {live && state.phase !== null && <span className={styles.phase}>{phaseText[state.phase]}</span>}
        {ended && <span className={styles.stale}>{copy.finalCount}</span>}
        {!live && !ended && <span className={styles.stale}>{copy.stale}</span>}
      </div>
      {live && state.phase !== null && state.barPercent !== null && (
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
      )}
      <dl className={styles.detect}>
        {/* Once the session has ended, how reps were counted live and the backup gesture no longer apply. */}
        {!ended && (
          <>
            <dt>{copy.countingBy}</dt>
            <dd>{sessionCopy.countingBy}</dd>
          </>
        )}
        <dt>{copy.lastRep}</dt>
        <dd>{state.last ? copy.last(state.last.rep, state.last.seconds) : copy.noLast}</dd>
        {!ended && (
          <>
            <dt>{copy.backup}</dt>
            <dd>{sessionCopy.backup}</dd>
          </>
        )}
      </dl>
      {!ended && (
        <div className={styles.minor}>
          <Button variant="quiet" disabled={!action.enabled || action.busy} onClick={onArtifact}>
            {copy.artefact}
          </Button>
          <Button variant="quiet">{copy.recount}</Button>
        </div>
      )}
    </Panel>
  );
}

function formatRest(seconds: number): string {
  return `${String(Math.floor(seconds / 60))}:${String(seconds % 60).padStart(2, '0')}`;
}

export function FinishStage({ session, action, onFinish }: { session: SessionData; action: StageAction; onFinish: () => void }): React.JSX.Element {
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
        <Button disabled={!action.enabled || action.busy} onClick={onFinish}>
          {copy.finish}
        </Button>
      </div>
      <p className={styles.hint}>{copy.hint}</p>
    </Panel>
  );
}
