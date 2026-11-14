import { createContext, useContext, useEffect, useState } from 'react';

import { realScheduler, type Scheduler } from './scheduler';

/** Source of the current time; injected so tests control it. */
export interface Clock {
  now(): Date;
  /** Calls `cb` with the new time on every tick; returns the unsubscribe function. */
  subscribe(cb: (now: Date) => void): () => void;
}

/** Wall clock that ticks every second. */
export function createSystemClock(scheduler: Scheduler = realScheduler): Clock {
  return {
    now: () => new Date(),
    subscribe: (cb) =>
      scheduler.every(1000, () => {
        cb(new Date());
      }),
  };
}

const ClockContext = createContext<Clock | null>(null);
const fallbackClock = createSystemClock();

/** Provides the clock (defaults to the wall clock). */
export function ClockProvider({ clock, children }: { clock?: Clock; children: React.ReactNode }): React.JSX.Element {
  return <ClockContext value={clock ?? fallbackClock}>{children}</ClockContext>;
}

export function useClock(): Clock {
  return useContext(ClockContext) ?? fallbackClock;
}

/** The current time, re-rendering the caller on every clock tick. */
export function useNow(): Date {
  const clock = useClock();
  const [now, setNow] = useState(() => clock.now());
  useEffect(() => clock.subscribe(setNow), [clock]);
  return now;
}
