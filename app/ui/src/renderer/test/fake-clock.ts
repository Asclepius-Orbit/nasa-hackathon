import type { Clock } from '../clock/Clock';

/** Clock driven by hand: `advance(ms)` moves time and notifies subscribers. */
export class FakeClock implements Clock {
  private current: Date;
  private listeners = new Set<(now: Date) => void>();
  constructor(start: string | Date = '2026-11-05T13:40:00Z') {
    this.current = new Date(start);
  }
  now(): Date {
    return this.current;
  }
  subscribe(cb: (now: Date) => void): () => void {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  }
  advance(ms: number): void {
    this.current = new Date(this.current.getTime() + ms);
    this.listeners.forEach((cb) => {
      cb(this.current);
    });
  }
  get listenerCount(): number {
    return this.listeners.size;
  }
}
