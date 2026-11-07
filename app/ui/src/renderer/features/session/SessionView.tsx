import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

import { Button } from '../../components/ui/Button';
import { Stepper } from '../../components/ui/Stepper';
import type { DataSource } from '../../data/DataSource';
import { useData } from '../../data/DataContext';
import { useLoad } from '../../data/useLoad';
import { session as copy, voiceUndo } from '../../strings/session';
import { CalibrateStage, CheckStage, FinishStage, FitStage, RepsStage, STAGE_IDS, type StageId } from './Stages';
import styles from './SessionView.module.css';
import { VoiceUndoAlert, useVoiceUndo } from './VoiceUndo';

const selectSession = (d: DataSource) => d.getSession();

function isStageId(value: string | null): value is StageId {
  return STAGE_IDS.some((id) => id === value);
}

export function SessionView(): React.JSX.Element {
  const { data: session, error } = useLoad(selectSession);
  const data = useData();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [chosen, setChosen] = useState<StageId | null>(null);

  // Stopping must always lead to the "after stop" screen, even if the record call fails.
  const stopForPain = (): void => {
    const go = (): void => {
      void navigate('/stopped');
    };
    data.stopSessionForPain().then(go, go);
  };
  const undo = useVoiceUndo(stopForPain);

  const queryStage = params.get('stage');
  const viewing = chosen ?? (isStageId(queryStage) ? queryStage : (session?.steps.find((s) => s.status === 'now')?.id ?? 'reps'));
  const index = STAGE_IDS.indexOf(viewing);
  const previous = index > 0 ? STAGE_IDS[index - 1] : undefined;
  const following = index < STAGE_IDS.length - 1 ? STAGE_IDS[index + 1] : undefined;

  return (
    <section aria-labelledby="session-title" data-testid="session-view">
      {error && <p role="alert">{copy.loadError}</p>}
      {!error && !session && <p>{copy.loading}</p>}
      {session && (
        <>
          <div className={styles.head}>
            <div>
              <h1 id="session-title">{session.title}</h1>
              <div className={styles.load}>
                <span>{copy.band(session.band.color, session.band.nominalKg)}</span>
                <span>{copy.stretched(session.stretchedCm)}</span>
                <span>{copy.reps(session.totalReps)}</span>
              </div>
              <p className={styles.late}>{copy.late(session.startedGmt, session.minutesBeforePlan, session.plannedGmt)}</p>
              <p className={styles.lede}>{copy.lede}</p>
            </div>
            <button type="button" className={styles.stopPain} onClick={stopForPain}>
              {copy.stopPain}
              <small>{copy.stopPainHint}</small>
            </button>
          </div>
          {undo.remaining !== null && <VoiceUndoAlert remaining={undo.remaining} onKeepGoing={undo.cancel} onStopNow={stopForPain} />}
          <div className={styles.proc}>
            <div>
              <Stepper label={copy.stepsLabel} steps={session.steps} viewing={viewing} onSelect={setChosen} />
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
