/** Repeating timer behind an interface so tests can drive time by hand. */
export interface Scheduler {
  /** Calls `fn` every `ms` milliseconds until the returned function is called. */
  every(ms: number, fn: () => void): () => void;
}

export const realScheduler: Scheduler = {
  every(ms, fn) {
    const id = setInterval(fn, ms);
    return () => {
      clearInterval(id);
    };
  },
};
