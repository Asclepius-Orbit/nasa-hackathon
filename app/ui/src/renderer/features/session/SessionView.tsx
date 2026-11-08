import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Stepper } from '../../components/ui/Stepper';
import type { DataSource } from '../../data/DataSource';
import { useData } from '../../data/DataContext';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { protocolTitle } from '../../strings/codes';
import { gmtClock, minutesBetween } from '../today/gmt';
import { session as copy, voiceUndo } from '../../strings/session';
import { CalibrateStage, CheckStage, FinishStage, FitStage, RepsStage, STAGE_IDS, type StageId } from './Stages';
import styles from './SessionView.module.css';
import { VoiceUndoAlert, useVoiceUndo } from './VoiceUndo';

/** Wrapped so that "loading" (no data) differs from "no session in progress" (`session: null`). */
const selectSession = (d: DataSource) => d.getSession().then((session) => ({ session }));

function isStageId(value: string | null): value is StageId {
  return STAGE_IDS.some((id) => id === value);
}

export function SessionView(): React.JSX.Element {
  const { data: loaded, error } = useLoad(selectSession, { refreshMs: REFRESH_MS });
  const session = loaded === null ? undefined : loaded.session;
  const data = useData();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [chosen, setChosen] = useState<StageId | null>(null);

  const [stopFailed, setStopFailed] = useState(false);

  // The "after stop" screen is only for a stop that was recorded. If recording fails the crew stays here and is told so,
  // never shown a screen that says the session was saved as stopped for pain when it was not.
  const stopForPain = (): void => {
    setStopFailed(false);
    data.stopSessionForPain().then(
      () => {
        void navigate('/stopped');
      },
      () => {
        setStopFailed(true);
      },
    );
  };
  const undo = useVoiceUndo(stopForPain);

  const queryStage = params.get('stage');
  const viewing = chosen ?? (isStageId(queryStage) ? queryStage : (session?.steps.find((s) => s.status === 'now')?.id ?? 'reps'));
  const index = STAGE_IDS.indexOf(viewing);
  const previous = index > 0 ? STAGE_IDS[index - 1] : undefined;
  const following = index < STAGE_IDS.length - 1 ? STAGE_IDS[index + 1] : undefined;

  return (
    <section aria-labelledby="session-title" data-testid="session-view">
      {error && <LoadFailure error={error} fallback={copy.loadError} />}
      {!error && session === undefined && <p>{copy.loading}</p>}
      {session === null && (
        <>
          <h1 id="session-title">{copy.title}</h1>
          <p role="status">{copy.none}</p>
          <p className={styles.lede}>{copy.noneHint}</p>
        </>
      )}
      {session && (
        <>
          <div className={styles.head}>
            <div>
              <h1 id="session-title">{protocolTitle(session.protocolId)}</h1>
              <div className={styles.load}>
                {session.band !== null && <span>{copy.band(session.band.color, session.band.nominalKg)}</span>}
                {session.stretchedCm !== null && <span>{copy.stretched(session.stretchedCm)}</span>}
                {session.totalReps !== null && <span>{copy.reps(session.totalReps)}</span>}
              </div>
              {session.startedAt !== null && (
                <p className={styles.late}>
                  {session.plannedAt === null
                    ? copy.started(gmtClock(session.startedAt))
                    : copy.late(gmtClock(session.startedAt), minutesBetween(session.plannedAt, session.startedAt), gmtClock(session.plannedAt))}
                </p>
              )}
              <p className={styles.lede}>{copy.lede}</p>
            </div>
            <button type="button" className={styles.stopPain} onClick={stopForPain}>
              {copy.stopPain}
              <small>{copy.stopPainHint}</small>
            </button>
          </div>
          {stopFailed && (
            <Alert
              title={copy.stopNotRecorded.title}
              action={
                <Button variant="quiet" onClick={stopForPain}>
                  {copy.stopNotRecorded.retry}
                </Button>
              }
            >
              {copy.stopNotRecorded.body}
            </Alert>
          )}
          {undo.remaining !== null && <VoiceUndoAlert remaining={undo.remaining} onKeepGoing={undo.cancel} onStopNow={stopForPain} />}
          <div className={styles.proc}>
            <div>
              <Stepper label={copy.stepsLabel} steps={session.steps.map((s) => ({ ...s, label: copy.stepLabels[s.id] }))} viewing={viewing} onSelect={setChosen} />
              <p className={styles.hint}>
                {copy.stepsHint} <span className={styles.pending}>{copy.pending}</span>
              </p>
            </div>
            <div>
              {viewing === 'fit' && <FitStage session={session} />}
              {viewing === 'check' && <CheckStage session={session} />}
              {viewing === 'mvc' && <CalibrateStage session={session} />}
              {viewing === 'reps' && <RepsStage session={session} />}
              {viewing === 'finish' && <FinishStage session={session} />}
              <div className={styles.actions}>
                {previous && (
                  <Button
                    variant="quiet"
                    onClick={() => {
                      setChosen(previous);
                    }}
                  >
                    {copy.back}
                  </Button>
                )}
                {following && (
                  <Button
                    variant="quiet"
                    onClick={() => {
                      setChosen(following);
                    }}
                  >
                    {copy.next}
                  </Button>
                )}
              </div>
            </div>
          </div>
          {import.meta.env.DEV && (
            <div className={styles.minor}>
              <Button variant="quiet" onClick={undo.start}>
                {voiceUndo.mockTrigger}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
