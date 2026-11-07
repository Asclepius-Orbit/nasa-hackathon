import type { Scheduler } from '../clock/scheduler';

/** Scheduler driven by hand: `advance(ms)` fires every due interval. */
export class FakeScheduler implements Scheduler {
  private timers = new Map<number, { ms: number; fn: () => void; elapsed: number }>();
  private nextId = 1;
  every(ms: number, fn: () => void): () => void {
    const id = this.nextId++;
    this.timers.set(id, { ms, fn, elapsed: 0 });
    return () => {
      this.timers.delete(id);
    };
  }
  advance(ms: number): void {
    for (let step = 0; step < ms; step += 1) {
      for (const timer of [...this.timers.values()]) {
        timer.elapsed += 1;
        if (timer.elapsed >= timer.ms) {
          timer.elapsed = 0;
          timer.fn();
        }
      }
    }
  }
  get active(): number {
    return this.timers.size;
  }
}
