import { useState } from 'react';

import { LoadFailure } from '../../components/layout/NoMonitor';
import { Button } from '../../components/ui/Button';
import { Panel } from '../../components/ui/Panel';
import { useData } from '../../data/DataContext';
import type { DataSource } from '../../data/DataSource';
import type { ContactEntry } from '../../data/types';
import { useLoad } from '../../data/useLoad';
import { stopped as copy } from '../../strings/stopped';
import form from '../report/Form.module.css';
import { useUrgent } from '../urgent/UrgentContext';
import styles from './StoppedView.module.css';

const selectStopped = (data: DataSource): ReturnType<DataSource['getStopped']> => data.getStopped();

/**
 * What to do after a stop for pain: where it stopped, who to talk to (the crew medical officer call is logged, the flight
 * surgeon is reached through Urgent) and what happens next. It asks no questions: the stop itself was recorded on the Session screen.
 */
export function StoppedView(): React.JSX.Element {
  const source = useData();
  const { open } = useUrgent();
  const stopped = useLoad(selectStopped);
  const [logged, setLogged] = useState<readonly ContactEntry[]>([]);
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
          <p className={form.lede}>{copy.lede(data.sessionId !== null, data.rep)}</p>
          <div className={styles.two}>
            <Panel>
              <h3>{copy.talk}</h3>
              <p className={styles.info}>{copy.link(data.groundLink)}</p>
              <div className={styles.actions}>
                <Button
                  onClick={() => {
                    setLogFailed(false);
                    source.logCrewMedicalOfficerCall(data.sessionId).then(
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
        </>
      )}
    </section>
  );
}
