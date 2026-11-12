import { useState } from 'react';

import { Scale } from '../../components/ui/Scale';
import pick from '../../components/ui/Pick.module.css';
import { useData } from '../../data/DataContext';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { report as copy } from '../../strings/report';
import styles from './Form.module.css';
import { PickRow } from './PickRow';
import type { ExerciseEntry, ReportSaved } from '../../../shared/api';
import { buildDailyReport, exerciseNeedsMinutes, REPORT_POINTERS, sleepNeedsHours } from './report-fields';
import { SaveBar } from './SaveBar';
import { useFormSave } from './useFormSave';
import { NO_SYMPTOM_ANSWER, SymptomQuestion, type SymptomAnswer } from './SymptomQuestion';

const selectForm = (data: DataSource): ReturnType<DataSource['getReportForm']> => data.getReportForm();

export function ReportView(): React.JSX.Element {
  const { data: form, error } = useLoad(selectForm);
  const [pain, setPain] = useState<number | null>(null);
  const [stiffness, setStiffness] = useState<number | null>(null);
  const [symptoms, setSymptoms] = useState<SymptomAnswer>(NO_SYMPTOM_ANSWER);
  const [where, setWhere] = useState<ReadonlySet<string>>(new Set());
  const [sleepHours, setSleepHours] = useState<number | null>(null);
  const [sleepQuality, setSleepQuality] = useState<number | null>(null);
  const [device, setDevice] = useState<number | null>(null);
  const [minutes, setMinutes] = useState<number | null>(null);
  const [askedHours, setAskedHours] = useState(false);
  const [leftOver, setLeftOver] = useState<{ entry: ExerciseEntry; why: 'not_stored' | 'no_context'; retrying: boolean } | null>(null);
  const source = useData();
  const save = useFormSave(REPORT_POINTERS, copy.failure);
  const fieldError = (pointer: (typeof REPORT_POINTERS)[number]): string | undefined => save.state.errors.byPointer[pointer];
  const hoursError = askedHours && sleepNeedsHours({ sleepHours, sleepQuality }) ? copy.sleepHours.needed : undefined;

  const keepExercise = (entry: ExerciseEntry | null, exercise: ReportSaved['exercise']): void => {
    setLeftOver(entry === null || exercise === 'stored' || exercise === null ? null : { entry, why: exercise, retrying: false });
  };
  const retryExercise = (): void => {
    if (leftOver === null) return;
    const { entry } = leftOver;
    setLeftOver({ ...leftOver, retrying: true });
    source.addExercise(entry).then(
      (result) => {
        keepExercise(entry, result.ok ? 'stored' : result.reason === 'no_context' ? 'no_context' : 'not_stored');
      },
      () => {
        keepExercise(entry, 'not_stored');
      },
    );
  };

  const missing = ([
    pain === null ? copy.left.pain : null,
    stiffness === null ? copy.left.stiffness : null,
    !symptoms.none && symptoms.codes.length === 0 ? copy.left.symptoms : null,
    form !== null && exerciseNeedsMinutes(form, { device, minutes }) ? copy.left.exerciseMinutes : null,
  ] as const).filter((part) => part !== null);

  const toggleCell = (key: string): void => {
    const next = new Set(where);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setWhere(next);
  };

  return (
    <section aria-labelledby="report-title" data-testid="report-view">
      <h1 id="report-title">{copy.title}</h1>
      <p className={styles.lede}>{copy.lede}</p>
      {error !== null && <p role="alert">{copy.failed}</p>}
      {form === null && error === null && <p role="status">{copy.loading}</p>}
      {form !== null && (
        <>
          <fieldset disabled={save.state.status === 'saving'} onChangeCapture={save.changed} onClickCapture={save.changed} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className={styles.q}>
            <p className={styles.legend}>{copy.pain.question}</p>
            <Scale label={copy.pain.question} value={pain} onChange={setPain} lowLabel={copy.pain.low} highLabel={copy.pain.high} />
            <FieldError message={fieldError('/pain_nrs')} />
            {pain !== null && pain > 0 && (
              <div className={styles.where}>
                <p className={styles.hint}>{copy.pain.whereHint}</p>
                <div role="group" aria-label={copy.pain.whereGroup} className={styles.backMap}>
                  <span />
                  {form.painSides.map((side) => (
                    <span key={side.value} className={styles.head}>
                      {side.label}
                    </span>
                  ))}
                  {form.painRegions.map((region) => (
                    <RegionRow key={region.value} label={region.label}>
                      {form.painSides.map((side) => {
                        const key = `${region.value}:${side.value}`;
                        return (
                          <button
                            key={key}
                            type="button"
                            aria-label={copy.pain.cell(region.label, side.label)}
                            aria-pressed={where.has(key)}
                            className={pick.pick}
                            onClick={() => {
                              toggleCell(key);
                            }}
                          />
                        );
                      })}
                    </RegionRow>
                  ))}
                </div>
              </div>
            )}
            <FieldError message={fieldError('/pain_locations')} />
          </div>

          <div className={styles.q}>
            <p className={styles.legend}>{copy.stiffness.question}</p>
            <Scale
              label={copy.stiffness.question}
              value={stiffness}
              onChange={setStiffness}
              lowLabel={copy.stiffness.low}
              highLabel={copy.stiffness.high}
            />
            <FieldError message={fieldError('/stiffness_nrs')} />
          </div>

          <div className={styles.q}>
            <p className={styles.legend}>{copy.symptoms.question}</p>
            <SymptomQuestion options={form.catalog.flags} answer={symptoms} onChange={setSymptoms} />
            <FieldError message={fieldError('/red_flags')} />
          </div>

          <div className={styles.q}>
            <p className={styles.legend}>
              {copy.sleepHours.question} <span className={styles.opt}>{copy.sleepHours.optional}</span>
            </p>
            <PickRow
              label={copy.sleepHours.question}
              options={form.sleepHours}
              selected={sleepHours}
              onSelect={setSleepHours}
            />
            <FieldError message={hoursError ?? fieldError('/sleep/hours')} />
          </div>

          <div className={styles.q}>
            <p className={styles.legend}>
              {copy.sleepQuality.question} <span className={styles.opt}>{copy.sleepHours.optional}</span>
            </p>
            <Scale
              label={copy.sleepQuality.question}
              value={sleepQuality}
              onChange={setSleepQuality}
              lowLabel={copy.sleepQuality.low}
              highLabel={copy.sleepQuality.high}
            />
            <FieldError message={fieldError('/sleep/quality_nrs')} />
          </div>

          <div className={styles.q}>
            <p className={styles.legend}>
              {copy.exercise.question} <span className={styles.opt}>{copy.exercise.optional}</span>
            </p>
            <PickRow label={copy.exercise.device} options={form.exerciseKinds} selected={device} onSelect={setDevice} />
            <PickRow label={copy.exercise.duration} options={form.exerciseMinutes} selected={minutes} onSelect={setMinutes} />
          </div>

          </fieldset>
          <SaveBar
            label={copy.save}
            canSave={missing.length === 0}
            note={missing.length > 0 ? copy.left.list(missing) : copy.left.ready}
            busy={save.state.status === 'saving'}
            busyLabel={copy.saving}
            problem={{ failure: save.state.failure, errors: save.state.errors, fixMarked: copy.fixMarked }}
            onSave={() => {
              if (pain === null || stiffness === null) return;
              setAskedHours(true);
              if (sleepNeedsHours({ sleepHours, sleepQuality })) return;
              const fields = buildDailyReport(form, { pain, stiffness, where, symptoms, sleepHours, sleepQuality, device, minutes });
              setLeftOver(null);
              save.run(async () => {
                const result = await source.saveDailyReport(fields);
                if (result.ok) keepExercise(fields.exercise, result.value.exercise);
                return result;
              });
            }}
            savedMessage={save.state.status === 'saved' && leftOver === null ? copy.saved : null}
            partial={
              leftOver !== null
                ? {
                    title: copy.exerciseNotSaved.title,
                    message: leftOver.why === 'no_context' ? copy.exerciseNotSaved.noContext : copy.exerciseNotSaved.notStored,
                    retryLabel: copy.exerciseNotSaved.retry,
                    retryBusyLabel: copy.exerciseNotSaved.retrying,
                    retrying: leftOver.retrying,
                    onRetry: retryExercise,
                  }
                : undefined
            }
          />
        </>
      )}
    </section>
  );
}

function FieldError({ message }: { message: string | undefined }): React.JSX.Element | null {
  return message === undefined ? null : <p className={styles.fieldError}>{message}</p>;
}

function RegionRow({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <>
      <span className={styles.lbl}>{label}</span>
      {children}
    </>
  );
}
