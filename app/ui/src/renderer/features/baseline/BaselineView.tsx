import { Meter } from '../../components/ui/Meter';
import { Panel } from '../../components/ui/Panel';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { baseline, recordedHeading, referenceLine, sessionLine, sessionName } from '../../strings/baseline';
import styles from './BaselineView.module.css';

const selectBaseline = (d: DataSource): ReturnType<DataSource['getBaseline']> => d.getBaseline();

/** The preflight reference: how many 1 g sessions are recorded and which reference version the assessments use. */
export function BaselineView(): React.JSX.Element {
  const { data, error } = useLoad(selectBaseline);
  return (
    <section aria-labelledby="baseline-title" data-testid="baseline-view">
      <h1 id="baseline-title">{baseline.title}</h1>
      <p className={styles.lede}>{baseline.lede}</p>
      {error !== null && <LoadFailure error={error} fallback={baseline.error} />}
      {error === null && data === null && <p role="status">{baseline.loading}</p>}
      {data !== null && (
        <Panel className={styles.panel}>
          <h2>{recordedHeading(data.recorded, data.required)}</h2>
          <div className={styles.progress}>
            <Meter label={baseline.progressLabel} value={data.recorded} max={data.required} />
          </div>
          <ul className={styles.kv} aria-label={baseline.sessionsLabel}>
            {data.sessions.map((s, i) => (
              <li key={s.sessionId ?? `planned-${String(s.offsetDays)}`}>
                <span className={styles.term}>{sessionName(i)}</span>
                <span className={styles.desc}>{sessionLine(s)}</span>
              </li>
            ))}
          </ul>
          <div className={styles.kv}>
            <span className={styles.term}>{baseline.reference}</span>
            <span className={styles.desc}>{referenceLine(data.referenceVersion, data.builtFromSessions)}</span>
          </div>
          <p className={styles.note}>{baseline.keepNote}</p>
        </Panel>
      )}
    </section>
  );
}
