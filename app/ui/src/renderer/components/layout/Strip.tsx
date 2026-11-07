import { useEffect, useState } from 'react';

import { useApi } from '../../api/ApiContext';
import { useAuth } from '../../auth/AuthProvider';
import { useNow } from '../../clock/Clock';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { useUrgent } from '../../features/urgent/UrgentContext';
import { useSettings } from '../../settings/SettingsProvider';
import { groundLinkValue, harnessValue, strip, urgentButton } from '../../strings/common';
import { BatteryIcon, GroundLinkIcon } from './icons';
import styles from './Strip.module.css';

const selectStrip = (data: DataSource): ReturnType<DataSource['getStrip']> => data.getStrip();

function Fact({ label, value }: { label: string; value: string }): React.JSX.Element {
  return (
    <span className={styles.fact}>
      <span>{label}</span>
      <span>{value}</span>
    </span>
  );
}

/** Name of the signed-in crew member from the bridge; falls back to the crew code, never blocks the strip. */
function useCrewName(crewId: string | null): string | null {
  const api = useApi();
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    if (crewId === null) return;
    let live = true;
    api.crew.list().then(
      (list) => {
        if (live) setName(list.find((m) => m.id === crewId)?.name ?? crewId);
      },
      () => {
        if (live) setName(crewId);
      },
    );
    return () => {
      live = false;
    };
  }, [api, crewId]);
  return name;
}

/** Always-visible top strip: who, mission day, GMT clock, phase, harness, ground link and the red Urgent button. */
export function Strip(): React.JSX.Element {
  const { crewId } = useAuth();
  const name = useCrewName(crewId);
  const { data } = useLoad(selectStrip);
  const now = useNow();
  const { formatTime } = useSettings();
  const { open } = useUrgent();

  return (
    <header className={styles.strip}>
      <span className={styles.who}>{name}</span>
      {data !== null && (
        <>
          <Fact label={strip.missionDay} value={String(data.missionDay)} />
          <Fact label={strip.gmt} value={formatTime(now, new Date(data.missionStart))} />
          <Fact label={strip.phase} value={data.flightPhase} />
          <span className={styles.grow} />
          <span className={styles.status}>
            <BatteryIcon />
            <Fact label={data.harness ? strip.harness(data.harness.id) : strip.harnessNone} value={harnessValue(data.harness)} />
          </span>
          <span className={styles.status}>
            <GroundLinkIcon />
            <Fact label={strip.groundLink} value={groundLinkValue(data.groundLink)} />
          </span>
        </>
      )}
      {data === null && <span className={styles.grow} />}
      <button
        type="button"
        className={styles.urgent}
        onClick={() => {
          open();
        }}
      >
        {urgentButton}
      </button>
    </header>
  );
}
