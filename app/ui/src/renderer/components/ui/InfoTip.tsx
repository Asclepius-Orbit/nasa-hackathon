import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import styles from './InfoTip.module.css';

const MARGIN = 12;

/**
 * Small "i" button with on-demand help. Opens on hover, focus or click/Enter (click pins it), closes with Escape,
 * a click outside, or leaving it. The bubble is a `role="tooltip"` referenced by `aria-describedby` and is kept inside the viewport.
 */
export function InfoTip({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [shift, setShift] = useState(0);
  const root = useRef<HTMLSpanElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);

  const close = (): void => {
    setOpen(false);
    setPinned(false);
    setShift(0);
  };

  // Measured once per opening, while the bubble is still unshifted (it is reset to 0 on close).
  useLayoutEffect(() => {
    if (!open || bubble.current === null) return;
    const rect = bubble.current.getBoundingClientRect();
    const width = document.documentElement.clientWidth;
    let next = 0;
    if (rect.right > width - MARGIN) next = width - MARGIN - rect.right;
    if (rect.left + next < MARGIN) next = MARGIN - rect.left;
    setShift(next);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent): void => {
      if (root.current !== null && event.target instanceof Node && !root.current.contains(event.target)) close();
    };
    document.addEventListener('pointerdown', outside);
    return () => {
      document.removeEventListener('pointerdown', outside);
    };
  }, [open]);

  return (
    // The wrapper only tracks hover/Escape for the real <button> inside; the button is the interactive control.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <span
      ref={root}
      className={styles.root}
      onMouseEnter={() => {
        setOpen(true);
      }}
      onMouseLeave={() => {
        if (!pinned) close();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) {
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        type="button"
        className={styles.trigger}
        aria-label={label}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onFocus={() => {
          setOpen(true);
        }}
        onBlur={() => {
          if (!pinned) close();
        }}
        onClick={() => {
          if (pinned) close();
          else {
            setPinned(true);
            setOpen(true);
          }
        }}
      >
        <span aria-hidden="true">i</span>
      </button>
      {open && (
        <span ref={bubble} id={id} role="tooltip" className={styles.bubble} style={{ transform: `translateX(${String(shift)}px)` }}>
          {children}
        </span>
      )}
    </span>
  );
}
