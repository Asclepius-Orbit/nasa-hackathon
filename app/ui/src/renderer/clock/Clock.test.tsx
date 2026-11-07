import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { FakeClock } from '../test/fake-clock';
import { FakeScheduler } from '../test/fake-scheduler';
import { ClockProvider, createSystemClock, useNow } from './Clock';
import { realScheduler } from './scheduler';

afterEach(() => {
  vi.useRealTimers();
});

function Now(): React.JSX.Element {
  return <p>{useNow().toISOString().slice(11, 16)}</p>;
}

it('re-renders on every tick and unsubscribes on unmount', () => {
  const clock = new FakeClock('2026-11-05T13:40:00Z');
  const { unmount } = render(
    <ClockProvider clock={clock}>
      <Now />
    </ClockProvider>,
  );
  expect(screen.getByText('13:40')).toBeInTheDocument();
  act(() => {
    clock.advance(60_000);
  });
  expect(screen.getByText('13:41')).toBeInTheDocument();
  unmount();
  expect(clock.listenerCount).toBe(0);
});

it('the system clock ticks through its scheduler once a second', () => {
  const scheduler = new FakeScheduler();
  const clock = createSystemClock(scheduler);
  const seen: Date[] = [];
  const stop = clock.subscribe((d) => seen.push(d));
  scheduler.advance(3000);
  expect(seen).toHaveLength(3);
  stop();
  scheduler.advance(2000);
  expect(seen).toHaveLength(3);
  expect(clock.now()).toBeInstanceOf(Date);
});

it('falls back to the wall clock without a provider', () => {
  render(<Now />);
  expect(screen.getByText(/^\d\d:\d\d$/)).toBeInTheDocument();
});

it('the real scheduler fires every interval until stopped', () => {
  vi.useFakeTimers();
  const fn = vi.fn();
  const stop = realScheduler.every(450, fn);
  vi.advanceTimersByTime(1350);
  expect(fn).toHaveBeenCalledTimes(3);
  stop();
  vi.advanceTimersByTime(4500);
  expect(fn).toHaveBeenCalledTimes(3);
});

it('the system clock reports the current time and ticks with it', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-11-05T13:40:00Z'));
  const clock = createSystemClock();
  expect(clock.now().toISOString()).toBe('2026-11-05T13:40:00.000Z');
  const seen: string[] = [];
  const stop = clock.subscribe((d) => seen.push(d.toISOString()));
  vi.advanceTimersByTime(2000);
  expect(seen).toEqual(['2026-11-05T13:40:01.000Z', '2026-11-05T13:40:02.000Z']);
  stop();
});
