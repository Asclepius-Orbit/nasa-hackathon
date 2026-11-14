import { useEffect, useState } from 'react';

import type { DeviceStatus } from '../../../shared/api';
import { useApi } from '../../api/ApiContext';
import { useNow } from '../../clock/Clock';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { Button } from '../../components/ui/Button';
import type { DataSource } from '../../data/DataSource';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { settingsText } from '../../strings/settings';
import { gmtStamp } from '../today/gmt';
import styles from './Settings.module.css';

const selectDevices = (d: DataSource): ReturnType<DataSource['listDevices']> => d.listDevices();
const t = settingsText.harness;

/** The harness id of the signed-in member, as `api.crew.list()` holds it. `undefined` until read. */
function useCurrentHarness(): [string | null | undefined, (id: string | null) => void] {
  const api = useApi();
  const [current, setCurrent] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    void Promise.all([api.auth.current(), api.crew.list()]).then(
      ([session, crew]) => {
        if (live) setCurrent(crew.find((m) => m.id === session?.crewId)?.harnessId ?? null);
      },
      () => {
        if (live) setCurrent(null);
      },
    );
    return () => {
      live = false;
    };
  }, [api]);
  return [current, setCurrent];
}

function DeviceRow({
  device,
  paired,
  busy,
  onPair,
}: {
  device: DeviceStatus;
  paired: boolean;
  busy: boolean;
  onPair: (id: string) => void;
}): React.JSX.Element {
  const now = useNow();
  return (
    <li className={styles.device} aria-label={device.device_id}>
      <b className={styles.deviceId}>{device.device_id}</b>
      <span>{t.battery(device.battery_pct)}</span>
      <span>{t.lastSeen(gmtStamp(device.timestamp, now))}</span>
      {device.status !== 'ok' && <strong className={styles.flag}>{device.status === 'degraded' ? t.degraded : t.offline}</strong>}
      {paired ? (
        <span className={styles.pairedTag}>{t.paired}</span>
      ) : (
        <Button
          variant="quiet"
          disabled={busy}
          onClick={() => {
            onPair(device.device_id);
          }}
        >
          {t.pair(device.device_id)}
        </Button>
      )}
    </li>
  );
}

/** Pairing of the signed-in member's harness: the devices seen on the bus and the one in use. Stored only on this device. */
export function HarnessSection(): React.JSX.Element {
  const api = useApi();
  const [current, setCurrent] = useCurrentHarness();
  const { data: devices, error } = useLoad(selectDevices, { refreshMs: REFRESH_MS });
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const pair = (harnessId: string | null): void => {
    setBusy(true);
    setFailure(null);
    api.crew.setHarness({ harnessId }).then(
      (result) => {
        setBusy(false);
        if (result.ok) setCurrent(result.member.harnessId);
        else setFailure(t.failure[result.reason]);
      },
      () => {
        setBusy(false);
        setFailure(t.failure.unavailable);
      },
    );
  };

  return (
    <section aria-labelledby="harness-title" className={styles.harness}>
      <h2 id="harness-title">{t.title}</h2>
      <div className={styles.pairing}>
        <p className={styles.current}>{current === undefined ? '' : current === null ? t.notPaired : t.pairedWith(current)}</p>
        {current !== undefined && current !== null && (
          <Button
            variant="quiet"
            disabled={busy}
            onClick={() => {
              pair(null);
            }}
          >
            {t.unpair}
          </Button>
        )}
      </div>
      {failure !== null && (
        <p className={styles.error} role="alert">
          {failure}
        </p>
      )}
      {error !== null && <LoadFailure error={error} fallback={t.devicesError} />}
      {devices !== null && devices.length === 0 && <p>{t.empty}</p>}
      {devices !== null && devices.length > 0 && (
        <ul className={styles.devices}>
          {devices.map((device) => (
            <DeviceRow key={device.device_id} device={device} paired={device.device_id === current} busy={busy} onPair={pair} />
          ))}
        </ul>
      )}
    </section>
  );
}
