/** Runs async tasks one at a time, in call order. Read-modify-write on a store must not interleave. */
export type Serial = <T>(task: () => Promise<T>) => Promise<T>;

export function createSerial(): Serial {
  let tail: Promise<unknown> = Promise.resolve();
  return <T>(task: () => Promise<T>): Promise<T> => {
    const run = tail.then(task);
    tail = run.catch(() => undefined);
    return run;
  };
}
