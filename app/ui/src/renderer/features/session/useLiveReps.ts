import { useEffect, useState } from 'react';

import { useData } from '../../data/DataContext';
import type { RepsUpdate } from '../../data/types';

/**
 * The live counter of a session, followed for as long as the session screen shows it (whatever stage is open), so the
 * rep is known when the crew stops for pain or marks an artefact. `null` until the first update, for another session than
 * the one that sent it, and when `counted` is false.
 */
export function useLiveReps(sessionId: string | null, counted: boolean): RepsUpdate | null {
  const data = useData();
  const [latest, setLatest] = useState<{ sessionId: string; update: RepsUpdate } | null>(null);
  useEffect(() => {
    if (sessionId === null || !counted) return undefined;
    return data.subscribeReps(sessionId, (update) => {
      setLatest({ sessionId, update });
    });
  }, [data, sessionId, counted]);
  return latest !== null && latest.sessionId === sessionId && counted ? latest.update : null;
}

/** The rep the counter is on (from 1), or `null` when it is not known: no count yet, rep 0, or the stream said nothing usable. */
export function currentRep(update: RepsUpdate | null): number | null {
  return update?.kind === 'state' && update.state.rep >= 1 ? update.state.rep : null;
}
