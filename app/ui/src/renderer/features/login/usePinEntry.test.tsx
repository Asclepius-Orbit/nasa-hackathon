import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { usePinEntry } from './usePinEntry';

function press(key: string, target: EventTarget = window): void {
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });
}

describe('usePinEntry', () => {
  it('hands over the PIN once on the 4th digit and clears the buffer', () => {
    const done = vi.fn();
    const { result } = renderHook(() => usePinEntry(done));
    press('1');
    press('2');
    press('x');
    press('3');
    expect(result.current.length).toBe(3);
    press('4');
    expect(done).toHaveBeenCalledExactlyOnceWith('1234');
    expect(result.current.length).toBe(0);
  });

  it('ignores keys typed into a text field', () => {
    const done = vi.fn();
    const { result } = renderHook(() => usePinEntry(done));
    const input = document.createElement('input');
    document.body.append(input);
    press('1', input);
    expect(result.current.length).toBe(0);
    input.remove();
  });

  it('ignores digits while disabled and Backspace removes one', () => {
    const done = vi.fn();
    const { result, rerender } = renderHook(({ on }) => usePinEntry(done, on), { initialProps: { on: false } });
    press('1');
    act(() => {
      result.current.digit('1');
    });
    expect(result.current.length).toBe(0);
    rerender({ on: true });
    press('1');
    press('2');
    press('Backspace');
    expect(result.current.length).toBe(1);
  });
});
