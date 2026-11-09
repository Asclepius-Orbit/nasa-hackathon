import { useAuth } from '../../auth/AuthProvider';
import { useNow } from '../../clock/Clock';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { Panel } from '../../components/ui/Panel';
import type { DataSource } from '../../data/DataSource';
import type { GroundSummaryData, HoustonData, OutboxItem } from '../../data/types';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { escalationRuleText, protocolShort, reasonText, recommendationText, redFlagLabels, sessionStateText, urgencyText } from '../../strings/codes';
import { levelLabel } from '../../strings/common';
import { houston, outboxState, REAL_TRANSPORTS } from '../../strings/houston';
import { gmtStamp } from '../today/gmt';
import styles from './Houston.module.css';

const selectHouston = (data: DataSource): ReturnType<DataSource['getHouston']> => data.getHouston();

/** The daily summary that leaves for Houston and its outbox. Read-only; it shows the crew code, never a name. */
export function HoustonView(): React.JSX.Element {
  const { data, error } = useLoad(selectHouston, { refreshMs: REFRESH_MS });
  const { crewId } = useAuth();
  const now = useNow();
  const crewCode = crewId ?? data?.crewCode ?? '';

  return (
    <section aria-labelledby="houston-title" data-testid="houston-view">
      <h1 id="houston-title">{houston.title}</h1>
      {error !== null && <LoadFailure error={error} fallback={houston.loadError} />}
      {data !== null && (
        <>
          <p className={styles.lede}>{houston.lede(crewCode)}</p>
          <div className={styles.notices}>
            {!data.contentValidated && <p className={styles.notice}>{houston.pendingValidation}</p>}
            {!REAL_TRANSPORTS.includes(data.transport) && <p className={styles.notice}>{houston.simulatedLink}</p>}
          </div>
          <div className={styles.two}>
            <SummaryPanel summary={data.summary} crewCode={crewCode} />
            <Panel as="section" aria-labelledby="hou-outbox">
              <h2 id="hou-outbox">{houston.outbox}</h2>
              <OutboxList items={data.outbox} now={now} />
              <p className={styles.note}>{linkText(data)}</p>
              {data.lastContactAt !== null && <p className={styles.note}>{houston.lastContact(gmtStamp(data.lastContactAt, now))}</p>}
            </Panel>
          </div>
        </>
      )}
    </section>
  );
}

function linkText({ groundLink }: HoustonData): string {
  if (groundLink === null) return houston.groundUnknown;
  return groundLink.state === 'up' ? houston.groundUp : houston.groundDown(groundLink.backInMin);
}

function OutboxList({ items, now }: { items: readonly OutboxItem[]; now: Date }): React.JSX.Element {
  if (items.length === 0) return <p className={styles.note}>{houston.outboxEmpty}</p>;
  return (
    <ul className={styles.queue} aria-label={houston.outbox}>
      {items.map((item) => (
        <li key={`${item.date}#${String(item.revision)}`}>
          <span>
            {houston.outboxLabel(item.missionDay, item.date, item.revision)}
            {item.receivedAt !== null && <span className={styles.aside}> ({houston.received(gmtStamp(item.receivedAt, now))})</span>}
          </span>
          <span className={styles.pill} data-state={item.state}>
            {outboxState(item)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function SummaryPanel({ summary, crewCode }: { summary: GroundSummaryData | null; crewCode: string }): React.JSX.Element {
  if (summary === null) {
    return (
      <Panel as="section" aria-labelledby="hou-summary">
        <h2 id="hou-summary">{houston.noSummary}</h2>
        <p className={styles.note}>{houston.noSummaryBody}</p>
      </Panel>
    );
  }
  const { escalation } = summary;
  return (
    <Panel as="section" aria-labelledby="hou-summary">
      <h2 id="hou-summary">{houston.summaryTitle(summary.missionDay, summary.date)}</h2>
      <dl className={styles.kv}>
        <dt>{houston.crew}</dt>
        <dd>
          {crewCode} <span className={styles.aside}>{houston.crewNote}</span>
        </dd>
        <dt>{houston.level}</dt>
        <dd>
          <span className={styles.tag} data-level={summary.level}>
            {levelLabel(summary.level)}
          </span>
          {summary.level !== 'nodata' && <>, {houston.confidence(summary.confidencePct, summary.dataAvailable, summary.dataTotal)}</>}
        </dd>
        <dt>{houston.drivers}</dt>
        <dd>{listOf(summary.drivers.map((d) => driverText(d.featureId, d.deviationZ)), houston.none)}</dd>
        <dt>{houston.warning}</dt>
        <dd>{listOf(summary.redFlags.map((flag) => redFlagLabels[flag]), houston.noneReported)}</dd>
        <dt>{houston.sessions}</dt>
        <dd>{listOf(summary.sessions.map((s) => `${protocolShort(s.protocolId)}, ${sessionStateText(s.state)}`), houston.none)}</dd>
        <dt>{houston.escalation}</dt>
        <dd>
          {escalation === null
            ? houston.notAssessed
            : escalation.required
              ? houston.escalationRequired(
                  escalation.urgency === null ? null : urgencyText(escalation.urgency),
                  escalation.reasons.map(escalationRuleText),
                )
              : houston.noEscalation}
        </dd>
        <dt>{houston.contacts}</dt>
        {/* Contacts are `null` until issue #63. */}
        <dd>{houston.notAvailable}</dd>
        <dt>{houston.plan}</dt>
        <dd>{listOf(summary.recommendations.map((code) => recommendationText(code).title), houston.none)}</dd>
      </dl>
    </Panel>
  );
}

function driverText(featureId: string, deviationZ: number | null): string {
  const { label, text } = reasonText(featureId, deviationZ);
  return `${label}: ${text}`;
}

/** One line per item, or the explicit text for an empty list. */
function listOf(items: readonly string[], empty: string): React.JSX.Element {
  if (items.length === 0) return <>{empty}</>;
  return (
    <ul className={styles.items}>
      {items.map((item, index) => (
        // Two items can read the same, so the text alone is not a key.
        <li key={`${String(index)}:${item}`}>{item}</li>
      ))}
    </ul>
  );
}
