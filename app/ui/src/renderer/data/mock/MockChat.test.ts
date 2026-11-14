import { describe, expect, it } from 'vitest';

import type { ChatEvent } from '../../../shared/api';
import { createMockChat, MOCK_ANSWER, type MockTimers } from './MockChat';

/** Timers driven by hand: `run()` fires the next due callback. */
class ManualTimers implements MockTimers {
  private queue: { id: number; fn: () => void }[] = [];
  private next = 1;
  setTimeout(fn: () => void): number {
    const id = this.next++;
    this.queue.push({ id, fn });
    return id;
  }
  clearTimeout(id: number): void {
    this.queue = this.queue.filter((t) => t.id !== id);
  }
  run(): void {
    this.queue.shift()?.fn();
  }
  runAll(): void {
    while (this.queue.length > 0) this.run();
  }
  get pending(): number {
    return this.queue.length;
  }
}

describe('createMockChat', () => {
  it('streams a tool chip, the canned answer in pieces and then ends', () => {
    const timers = new ManualTimers();
    const events: ChatEvent[] = [];
    createMockChat(timers).ask({ conversationId: 'c', question: 'How am I?' }, (event) => events.push(event));
    expect(events).toEqual([]);
    timers.runAll();
    expect(events[0]).toEqual({ kind: 'tool_call', toolName: 'get_current_status' });
    expect(events.at(-1)).toEqual({ kind: 'end' });
    const text = events.flatMap((e) => (e.kind === 'text' ? [e.delta] : [])).join('');
    expect(text).toBe(MOCK_ANSWER);
    expect(events.filter((e) => e.kind === 'text').length).toBeGreaterThan(1);
  });

  it('stops at once when cancelled, silently and idempotently', () => {
    const timers = new ManualTimers();
    const events: ChatEvent[] = [];
    const cancel = createMockChat(timers).ask({ conversationId: 'c', question: 'q' }, (event) => events.push(event));
    timers.run();
    cancel();
    cancel();
    expect(timers.pending).toBe(0);
    timers.runAll();
    expect(events).toHaveLength(1);
  });

  it('forgets without failing', async () => {
    await expect(createMockChat(new ManualTimers()).forget({ conversationId: 'c' })).resolves.toBeUndefined();
  });

  it('runs on real timers by default', async () => {
    const events: ChatEvent[] = [];
    const cancel = createMockChat().ask({ conversationId: 'c', question: 'q' }, (event) => events.push(event));
    await new Promise((resolve) => setTimeout(resolve, 600));
    cancel();
    expect(events.length).toBeGreaterThan(0);
  });
});
