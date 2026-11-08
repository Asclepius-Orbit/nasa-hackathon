import { useState } from 'react';

import { Button } from '../../components/ui/Button';
import { Panel } from '../../components/ui/Panel';
import { Scale } from '../../components/ui/Scale';
import { useData } from '../../data/DataContext';
import type { DataSource } from '../../data/DataSource';
import type { ContactEntry } from '../../data/types';
import { useLoad } from '../../data/useLoad';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { stopped as copy } from '../../strings/stopped';
import form from '../report/Form.module.css';
import { SaveBar } from '../report/SaveBar';
import { NO_SYMPTOM_ANSWER, SymptomQuestion, type SymptomAnswer } from '../report/SymptomQuestion';
import { report } from '../../strings/report';
import { useUrgent } from '../urgent/UrgentContext';
import styles from './StoppedView.module.css';

const selectStopped = (data: DataSource): ReturnType<DataSource['getStopped']> => data.getStopped();
const selectCatalog = (data: DataSource): ReturnType<DataSource['getCatalog']> => data.getCatalog();

export function StoppedView(): React.JSX.Element {
  const source = useData();
  const { open } = useUrgent();
  const stopped = useLoad(selectStopped);
  const catalog = useLoad(selectCatalog).data;
  const [pain, setPain] = useState<number | null>(null);
  const [symptoms, setSymptoms] = useState<SymptomAnswer>(NO_SYMPTOM_ANSWER);
  const [logged, setLogged] = useState<readonly ContactEntry[]>([]);
  const [saved, setSaved] = useState(false);
  const [logFailed, setLogFailed] = useState(false);
  const data = stopped.data;
  const contacts = data === null ? [] : [...data.contacts, ...logged];

  return (
    <section aria-labelledby="stopped-title" data-testid="stopped-view">
      <h1 id="stopped-title">{copy.title}</h1>
      {stopped.error !== null && <LoadFailure error={stopped.error} fallback={copy.failed} />}
      {data === null && stopped.error === null && <p role="status">{copy.loading}</p>}
      {data !== null && (
        <>
          <p className={form.lede}>{copy.lede(data.rep)}</p>
          <div className={form.q}>
            <p className={form.legend}>{copy.painQuestion}</p>
            <Scale label={copy.painQuestion} value={pain} onChange={setPain} lowLabel={copy.painLow} highLabel={copy.painHigh} />
          </div>
          <div className={form.q}>
            <p className={form.legend}>{report.symptoms.question}</p>
            {catalog !== null && <SymptomQuestion options={catalog.flags} answer={symptoms} onChange={setSymptoms} />}
          </div>
          <div className={styles.two}>
            <Panel>
              <h3>{copy.talk}</h3>
              <p className={styles.info}>{copy.link(data.groundLink, data.crewMedicalOfficerOnBoard)}</p>
              <div className={styles.actions}>
                <Button
                  onClick={() => {
                    setLogFailed(false);
                    source.logCrewMedicalOfficerCall().then(
                      (entry) => {
                        setLogged((prev) => [...prev, entry]);
                      },
                      () => {
                        setLogFailed(true);
                      },
                    );
                  }}
                >
                  {copy.callOfficer}
                </Button>
                <Button
                  variant="quiet"
                  onClick={() => {
                    open();
                  }}
                >
                  {copy.messageSurgeon}
                </Button>
              </div>
              {logFailed && <p role="alert">{copy.callNotLogged}</p>}
              {contacts.length > 0 && (
                <ul className={styles.log} aria-label={copy.contactsLabel}>
                  {contacts.map((entry) => (
                    <li key={`${entry.atGmt}-${entry.text}`}>{entry.text}</li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel>
              <h3>{copy.next}</h3>
              <p className={styles.info}>{copy.nextBody}</p>
            </Panel>
          </div>
          <SaveBar
            label={copy.save}
            canSave={pain !== null}
            note={pain === null ? copy.needed : copy.ready}
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
