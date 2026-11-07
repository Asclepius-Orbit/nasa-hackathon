import { useState } from 'react';

import { Scale } from '../../components/ui/Scale';
import pick from '../../components/ui/Pick.module.css';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { report as copy } from '../../strings/report';
import styles from './Form.module.css';
import { PickRow } from './PickRow';
import { SaveBar } from './SaveBar';
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
  const [saved, setSaved] = useState(false);

  const missing = ([
    pain === null ? copy.left.pain : null,
    stiffness === null ? copy.left.stiffness : null,
    !symptoms.none && symptoms.codes.length === 0 ? copy.left.symptoms : null,
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
          <div className={styles.q}>
            <p className={styles.legend}>{copy.pain.question}</p>
            <Scale label={copy.pain.question} value={pain} onChange={setPain} lowLabel={copy.pain.low} highLabel={copy.pain.high} />
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
          </div>

          <div className={styles.q}>
            <p className={styles.legend}>{copy.symptoms.question}</p>
            <SymptomQuestion options={form.catalog.flags} answer={symptoms} onChange={setSymptoms} />
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
          </div>

          <div className={styles.q}>
            <p className={styles.legend}>
              {copy.exercise.question} <span className={styles.opt}>{copy.exercise.optional}</span>
            </p>
            <PickRow label={copy.exercise.device} options={form.exerciseKinds} selected={device} onSelect={setDevice} />
            <PickRow label={copy.exercise.duration} options={form.exerciseMinutes} selected={minutes} onSelect={setMinutes} />
          </div>

          <SaveBar
            label={copy.save}
            canSave={missing.length === 0}
            note={missing.length > 0 ? copy.left.list(missing) : copy.left.ready}
            onSave={() => {
              setSaved(true);
            }}
            savedMessage={saved ? copy.saved : null}
          />
        </>
      )}
    </section>
  );
}

function RegionRow({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <>
      <span className={styles.lbl}>{label}</span>
      {children}
    </>
  );
}
