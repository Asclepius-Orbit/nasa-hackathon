import { useState } from 'react';

import type { HoustonReportResult } from '../../../shared/api';
import { Button } from '../../components/ui/Button';
import { useData } from '../../data/DataContext';
import { houstonReport as copy } from '../../strings/today';
import styles from './HoustonReportCard.module.css';

type Phase = { kind: 'idle' } | { kind: 'busy' } | { kind: 'done'; result: HoustonReportResult };

function message(result: HoustonReportResult): string {
  switch (result.status) {
    case 'saved':
      return copy.saved(result.fileName, result.opened);
    case 'cancelled':
      return copy.cancelled;
    case 'error':
      return copy.errors[result.reason];
  }
}

/** Generates the weekly PDF for Houston on demand. A separate deliverable: it never replaces the daily summary. */
export function HoustonReportCard(): React.JSX.Element {
  const data = useData();
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const busy = phase.kind === 'busy';

  const generate = (): void => {
    setPhase({ kind: 'busy' });
    // `generateHoustonReport` never rejects, but a failing bridge must not leave the button stuck.
    void data
      .generateHoustonReport()
      .catch((): HoustonReportResult => ({ status: 'error', reason: 'unavailable' }))
      .then((result) => {
        setPhase({ kind: 'done', result });
      });
  };

  const failed = phase.kind === 'done' && phase.result.status === 'error';
  return (
    <div className={styles.card} data-testid="houston-report">
      <h2>{copy.title}</h2>
      <p>{copy.body}</p>
      <Button variant="quiet" disabled={busy || !data.generatesHoustonReport} aria-busy={busy} onClick={generate}>
        {busy ? copy.busy : copy.button}
      </Button>
      {!data.generatesHoustonReport && <p className={styles.note}>{copy.demo}</p>}
      {/* Always rendered so a screen reader announces the change; a failure is an alert, the rest a status. */}
      <p className={styles.note} role={failed ? 'alert' : 'status'} data-failed={failed}>
        {phase.kind === 'done' ? message(phase.result) : ''}
      </p>
    </div>
  );
}
