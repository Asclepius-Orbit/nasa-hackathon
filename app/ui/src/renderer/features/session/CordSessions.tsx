import type { CordHistoryData, CordHistoryPostureData, CordPostureData, CordProtocolsData } from '../../data/types';
import { protocolTitle } from '../../strings/codes';
import { cord as copy } from '../../strings/cord';
import { gmtClock, gmtDay } from '../today/gmt';
import styles from './CordSessions.module.css';

const stamp = (iso: string): string => `${gmtDay(iso)} ${gmtClock(iso)}`;

function Estimate({ posture }: { posture: CordPostureData }): React.JSX.Element {
  const { estimate } = posture;
  if (estimate === null) {
    const reason = posture.estimateUnavailable;
    const text = reason === null ? undefined : copy.estimate.unavailable[reason];
    return <p className={styles.note}>{text ?? copy.estimate.unavailable.ESTIMATE_ENGINE_UNAVAILABLE}</p>;
  }
  return (
    <div className={styles.estimate} data-testid="cord-estimate">
      <p className={styles.estimateTitle}>{copy.estimate.title}</p>
      <p>{copy.estimate.value(estimate.estimatedLengthMm, estimate.elongationMm)}</p>
      <p className={styles.note}>{copy.estimate.disclaimer}</p>
      <p className={styles.note}>{copy.estimate.assumptionsHeading}</p>
      <ul className={styles.assumptions}>
        {estimate.assumptions.map((code) => (
          <li key={code}>{copy.estimate.assumptions[code] ?? code}</li>
        ))}
      </ul>
    </div>
  );
}

function History({ posture }: { posture: CordHistoryPostureData }): React.JSX.Element {
  const words = copy.history;
  return (
    <section className={styles.history} aria-labelledby={`cord-history-${posture.protocolId}`} data-testid="cord-history">
      <h3 id={`cord-history-${posture.protocolId}`} className={styles.title}>{protocolTitle(posture.protocolId)}</h3>
      {posture.measurements.length === 0 ? (
        <p className={styles.note}>{words.none}</p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">{words.columns.date}</th>
              <th scope="col">{words.columns.environment}</th>
              <th scope="col">{words.columns.resistance}</th>
              <th scope="col">{words.columns.quality}</th>
              <th scope="col">{words.columns.valid}</th>
            </tr>
          </thead>
          <tbody>
            {posture.measurements.map((m) => (
              <tr key={m.sessionId}>
                <td>{stamp(m.measuredAt)}</td>
                <td>{words.environments[m.gravity] ?? m.gravity}</td>
                <td>{m.resistanceOhm === null ? words.noResistance : `${String(Math.round(m.resistanceOhm))} ohm`}</td>
                <td>{m.qualityScore === null ? words.noResistance : words.quality(m.qualityScore)}</td>
                <td>
                  {m.valid ? words.yes : words.no}
                  {m.invalidReason !== null && <span className={styles.note}> {words.invalidReasons[m.invalidReason] ?? m.invalidReason}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Both cord sessions: when each is due, the last valid measurement of each and, if there is one, the calculated estimate. */
export function CordSessions({ cord, history = null }: { cord: CordProtocolsData; history?: CordHistoryData | null }): React.JSX.Element {
  return (
    <section className={styles.section} aria-labelledby="cord-title" data-testid="cord-sessions">
      <h2 id="cord-title">{copy.heading}</h2>
      <p className={styles.lede}>{copy.lede}</p>
      <p className={styles.note}>{copy.cadence(cord.cadenceDays)}</p>
      <ul className={styles.list}>
        {cord.postures.map((p) => (
          <li key={p.protocolId} className={styles.posture}>
            <div className={styles.head}>
              <h3 className={styles.title}>{protocolTitle(p.protocolId)}</h3>
              <span className={styles.badge} data-due={p.due}>
                {p.due ? copy.due : copy.upToDate}
              </span>
            </div>
            <p className={styles.note}>{copy.dueReasons[p.dueReason] ?? p.dueReason}</p>
            <dl className={styles.facts}>
              <dt>{copy.earthReference}</dt>
              <dd>{p.earthReference === null ? copy.none : copy.measurement(p.earthReference.resistanceOhm, stamp(p.earthReference.measuredAt))}</dd>
              <dt>{copy.lastFollowUp}</dt>
              <dd>{p.lastFollowUp === null ? copy.none : copy.measurement(p.lastFollowUp.resistanceOhm, stamp(p.lastFollowUp.measuredAt))}</dd>
            </dl>
            <Estimate posture={p} />
          </li>
        ))}
      </ul>
      {history !== null && (
        <div className={styles.historyBlock}>
          <h2 id="cord-history-title">{copy.history.heading}</h2>
          <p className={styles.lede}>{copy.history.lede}</p>
          {history.postures.map((p) => (
            <History key={p.protocolId} posture={p} />
          ))}
        </div>
      )}
    </section>
  );
}
