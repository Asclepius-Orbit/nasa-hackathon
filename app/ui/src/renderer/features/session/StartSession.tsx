import { useState } from 'react';
import { Link } from 'react-router';

import type { ProtocolId, StartSessionResult } from '../../../shared/api';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { useData } from '../../data/DataContext';
import { protocolTitle } from '../../strings/codes';
import { start as copy } from '../../strings/session';
import styles from './StartSession.module.css';

export const PROTOCOLS: readonly ProtocolId[] = ['lat_pulldown_standing_v1', 'spine_length_rest_v1'];

export function isProtocolId(value: string): value is ProtocolId {
  return PROTOCOLS.some((id) => id === value);
}

type Failure = Exclude<StartSessionResult, { ok: true }>['reason'];

export interface Starter {
  busy: boolean;
  failure: Failure | null;
  start: (protocolId: ProtocolId) => void;
}

/** Starts a session. `onStarted` runs only once the backend took the start; a refusal is kept to be told. */
export function useStarter(onStarted: () => void): Starter {
  const data = useData();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  return {
    busy,
    failure,
    start: (protocolId) => {
      setBusy(true);
      setFailure(null);
      data.startSession(protocolId).then(
        (result) => {
          setBusy(false);
          if (result.ok) onStarted();
          else setFailure(result.reason);
        },
        () => {
          setBusy(false);
          setFailure('unavailable');
        },
      );
    },
  };
}

/** Why a start did not happen, with where to fix it when the fix is on another screen. */
function StartFailure({ failure }: { failure: Failure }): React.JSX.Element {
  if (failure === 'no_harness') {
    return (
      <Alert title={copy.noHarness.title} action={<Link to="/settings">{copy.noHarness.link}</Link>}>
        {copy.noHarness.body}
      </Alert>
    );
  }
  if (failure === 'invalid') {
    return (
      <Alert title={copy.noContext.title} action={<Link to="/profile">{copy.noContext.link}</Link>}>
        {copy.noContext.body}
      </Alert>
    );
  }
  return <Alert>{copy.failure[failure]}</Alert>;
}

/** The choice of protocol and the Start button, shown when there is no session (or the last one is over). */
export function StartSession({ starter }: { starter: Starter }): React.JSX.Element {
  const [protocol, setProtocol] = useState<ProtocolId | null>(null);
  return (
    <div className={styles.panel}>
      <div role="radiogroup" aria-label={copy.group} className={styles.choices}>
        {PROTOCOLS.map((id) => (
          <label key={id} className={styles.choice} data-checked={protocol === id}>
            <input
              type="radio"
              name="protocol"
              checked={protocol === id}
              aria-labelledby={`protocol-title-${id}`}
              aria-describedby={`protocol-${id}`}
              onChange={() => {
                setProtocol(id);
              }}
              className={styles.radio}
            />
            <span id={`protocol-title-${id}`} className={styles.title}>{protocolTitle(id)}</span>
            <span id={`protocol-${id}`} className={styles.hint}>
              {copy.hints[id]}
            </span>
          </label>
        ))}
      </div>
      {starter.failure !== null && <StartFailure failure={starter.failure} />}
      <div className={styles.actions}>
        <Button
          disabled={protocol === null || starter.busy}
          onClick={() => {
            if (protocol !== null) starter.start(protocol);
          }}
        >
          {starter.busy ? copy.busy : copy.button}
        </Button>
      </div>
    </div>
  );
}
