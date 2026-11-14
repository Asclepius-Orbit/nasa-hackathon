import { useCallback, useEffect, useRef, useState } from 'react';

export const PIN_LENGTH = 4;

export interface PinEntry {
  /** How many digits are typed. The digits themselves never leave this hook. */
  length: number;
  digit: (d: string) => void;
  remove: () => void;
  enter: () => void;
  clear: () => void;
}

/**
 * Collects a 4-digit PIN from the on-screen pad and the physical keyboard.
 * `onComplete` gets the PIN once, then the buffer is cleared at once (the PIN is never kept or rendered).
 */
export function usePinEntry(onComplete: (pin: string) => void, enabled = true): PinEntry {
  const buffer = useRef('');
  const [length, setLength] = useState(0);
  const done = useRef(onComplete);
  useEffect(() => {
    done.current = onComplete;
  }, [onComplete]);

  const clear = useCallback(() => {
    buffer.current = '';
    setLength(0);
  }, []);

  const digit = useCallback(
    (d: string) => {
      if (!enabled || buffer.current.length >= PIN_LENGTH) return;
      buffer.current += d;
      if (buffer.current.length < PIN_LENGTH) {
        setLength(buffer.current.length);
        return;
      }
      const pin = buffer.current;
      clear();
      done.current(pin);
    },
    [enabled, clear],
  );

  const remove = useCallback(() => {
    buffer.current = buffer.current.slice(0, -1);
    setLength(buffer.current.length);
  }, []);

  // A PIN completes on its 4th digit, so Enter has nothing left to submit.
  const enter = useCallback(() => undefined, []);

  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
      if (/^\d$/.test(event.key)) digit(event.key);
      else if (event.key === 'Backspace') remove();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [enabled, digit, remove]);

  return { length, digit, remove, enter, clear };
}
