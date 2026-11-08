import { useAuth } from '../../auth/AuthProvider';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { Panel } from '../../components/ui/Panel';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { levelLabel } from '../../strings/common';
import { houston, outboxState } from '../../strings/houston';
import styles from './Houston.module.css';

const selectHouston = (data: DataSource): ReturnType<DataSource['getHouston']> => data.getHouston();

/** The daily summary that leaves for Houston and its outbox. Read-only; it shows the crew code, never a name. */
export function HoustonView(): React.JSX.Element {
  const { data, error } = useLoad(selectHouston);
  const { crewId } = useAuth();
  const crewCode = crewId ?? data?.crewCode ?? '';

  return (
    <section aria-labelledby="houston-title" data-testid="houston-view">
      <h1 id="houston-title">{houston.title}</h1>
      {error !== null && <LoadFailure error={error} fallback={houston.loadError} />}
      {data !== null && (
        <>
          <p className={styles.lede}>{houston.lede(crewCode)}</p>
          <div className={styles.two}>
            <Panel as="section" aria-labelledby="hou-summary">
              <h2 id="hou-summary">{houston.summaryTitle(data.day)}</h2>
              <dl className={styles.kv}>
                <dt>{houston.crew}</dt>
                <dd>
                  {crewCode} <span className={styles.aside}>{houston.crewNote}</span>
                </dd>
                <dt>{houston.level}</dt>
                <dd>
                  <span className={styles.tag} data-level={data.level}>
                    {levelLabel(data.level)}
                  </span>
                  , {houston.confidence(data.confidencePct, data.dataAvailable, data.dataTotal)}
                </dd>
                <dt>{houston.drivers}</dt>
                <dd>{data.drivers}</dd>
                <dt>{houston.warning}</dt>
                <dd>{data.warningSymptoms}</dd>
                <dt>{houston.sessions}</dt>
                <dd>{data.sessions}</dd>
                <dt>{houston.contacts}</dt>
                <dd>{data.contacts}</dd>
                <dt>{houston.plan}</dt>
                <dd>{data.plan.join(', ')}</dd>
              </dl>
            </Panel>
            <Panel as="section" aria-labelledby="hou-outbox">
              <h2 id="hou-outbox">{houston.outbox}</h2>
              <ul className={styles.queue} aria-label={houston.outbox}>
                {data.outbox.map((item) => (
                  <li key={item.label}>
                    <span>{item.label}</span>
                    <span className={styles.pill} data-state={item.state}>
                      {outboxState(item)}
                    </span>
                  </li>
                ))}
              </ul>
              <p className={styles.note}>
                {data.groundLink.state === 'up' ? houston.groundUp : houston.groundDown(data.groundLink.backInMin)} {houston.queueNote}
              </p>
            </Panel>
          </div>
        </>
      )}
    </section>
  );
}
