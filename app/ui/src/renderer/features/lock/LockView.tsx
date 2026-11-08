import { useState } from 'react';
import { useNavigate } from 'react-router';

import { useApi } from '../../api/ApiContext';
import { useAuth } from '../../auth/AuthProvider';
import { useNow } from '../../clock/Clock';
import { MockSkipLogin } from '../../components/layout/MockSkipLogin';
import { Button } from '../../components/ui/Button';
import { Keypad } from '../../components/ui/Keypad';
import { NoData } from '../../components/ui/Panel';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { formatTime } from '../../settings/format';
import { lock } from '../../strings/lock';
import { login, pinFailureMessage } from '../../strings/login';
import { PinDots } from '../login/PinDots';
import { usePinEntry } from '../login/usePinEntry';
import { useUrgent } from '../urgent/UrgentContext';
import styles from './Lock.module.css';

const selectStrip = (data: DataSource): ReturnType<DataSource['getStrip']> => data.getStrip();

/** Lock screen: only the PIN of the signed-in crew member unlocks it. Shows the time and mission day, never health data. */
export function LockView(): React.JSX.Element {
  const api = useApi();
  const { crewId, signIn } = useAuth();
  const navigate = useNavigate();
  const { open } = useUrgent();
  const now = useNow();
  const { data } = useLoad(selectStrip);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const verify = (pin: string): void => {
    if (crewId === null) return;
    setBusy(true);
    api.auth.verifyPin({ crewId, pin }).then(
      (result) => {
        setBusy(false);
        if (result.ok) {
          signIn(result);
          void navigate('/today');
        } else setError(pinFailureMessage(result));
      },
      () => {
        setBusy(false);
        setError(login.unavailable);
      },
    );
  };
  const pad = usePinEntry(verify, !busy);

  return (
    <section aria-labelledby="lock-title" data-testid="lock-view" className={styles.lock}>
      <div className={styles.inner}>
        <div className={styles.time}>{data === null ? <NoData /> : formatTime(now, 'gmt', null)}</div>
        {data?.missionDay != null && <p className={styles.day}>{lock.gmtLine(data.missionDay)}</p>}
        <h1 id="lock-title" className={styles.title}>
          {lock.title}
        </h1>
        <p className={styles.note}>{lock.noHealthData}</p>
        <div className={styles.dots}>
          <PinDots length={pad.length} />
        </div>
        <p className={styles.error} role="alert">
          {error}
        </p>
        <div className={styles.pad}>
          <Keypad onDigit={pad.digit} onDelete={pad.remove} onEnter={pad.enter} disabled={busy} />
        </div>
        <Button
          variant="urgent"
          className={styles.urgent}
          onClick={() => {
            open();
          }}
        >
          {lock.urgent}
        </Button>
        <MockSkipLogin />
      </div>
    </section>
  );
}
